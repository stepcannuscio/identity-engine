"""Query API routes, including streaming server-sent events."""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from config.llm_router import ConfigurationError, ProviderConfig
from db.preference_signals import PreferenceSignalInput, record_preference_signal
from db.query_feedback import QueryFeedbackInput, record_query_feedback
from db.voice_feedback import VoiceFeedbackInput, record_voice_feedback
from engine.coverage_evaluator import INSUFFICIENT_DATA_MESSAGE
from engine.privacy_broker import PrivacyBroker
from engine.prompt_builder import RoutingViolationError
from engine.query_engine import (
    QueryContext,
    apply_local_fallback_audit,
    build_forced_artifact_response_decision,
    build_insufficient_data_decision,
    prepare_query,
    record_blocked_query,
    record_query_result,
)
from engine.setup_state import resolve_active_provider_config
from server.db import get_db_connection
from server.models.schemas import (
    AcquisitionPlan,
    AcquisitionGap,
    AcquisitionSuggestion,
    CoverageCounts,
    QueryFeedbackRequest,
    QueryFeedbackResponse,
    QueryIntentMetadata,
    QueryMetadata,
    QueryRequest,
    QueryResponse,
)
from server.privacy import (
    blocked_privacy_state,
    privacy_state_from_decision,
    privacy_state_from_provider,
    unavailable_privacy_state,
)

router = APIRouter(tags=["query"])
logger = logging.getLogger(__name__)

_SENSITIVE_DOMAINS = {"beliefs", "fears", "patterns", "relationships"}
_SENSITIVE_TERMS = {
    "belief",
    "beliefs",
    "fear",
    "fears",
    "relationship",
    "relationships",
    "pattern",
    "patterns",
    "anxiety",
    "trauma",
}

_VOICE_FEEDBACK_SIGNAL_MAP = {
    "authentic": ("voice", "authentic_voice", "prefer", 4),
    "not_me": ("voice", "current_voice_render", "reject", 4),
    "too_formal": ("voice", "formal_tone", "avoid", 4),
    "too_wordy": ("voice", "wordy_phrasing", "avoid", 4),
    "wrong_rhythm": ("voice", "rhythm_mismatch", "reject", 4),
    "overdone_style": ("voice", "overdone_style_markers", "avoid", 4),
}


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _resolve_provider(conn, default_config: ProviderConfig, override: str | None) -> ProviderConfig:
    if override not in {None, "local", "external"}:
        raise HTTPException(status_code=400, detail="invalid backend_override")
    return resolve_active_provider_config(
        conn,
        default_config,
        backend_override=override,
    )


def _is_sensitive_query(query_text: str, attributes: list[dict]) -> bool:
    lowered = query_text.lower()
    if any(term in lowered for term in _SENSITIVE_TERMS):
        return True
    return any(str(attr.get("domain")) in _SENSITIVE_DOMAINS for attr in attributes)


def _metadata_from_context(context, duration_ms: int, privacy) -> QueryMetadata:
    domains = list(getattr(context.assembled_context, "domains_used", []))
    coverage = context.coverage
    acquisition = getattr(context, "acquisition", None)
    if acquisition is None:
        acquisition = AcquisitionPlan(status="not_needed", gaps=[], suggestions=[])
    return QueryMetadata(
        query_type=context.query_type,
        intent=QueryIntentMetadata(
            source_profile=getattr(context, "source_profile", "general"),
            intent_tags=list(getattr(context, "intent_tags", [])),
            domain_hints=list(getattr(context, "domain_hints", [])),
        ),
        attributes_used=len(context.attributes),
        backend_used=context.backend,
        requested_backend=getattr(context, "requested_backend", context.backend),
        domains_referenced=domains,
        duration_ms=duration_ms,
        privacy=privacy,
        confidence=coverage.confidence,
        coverage=CoverageCounts(
            attributes=coverage.counts.attributes,
            preferences=coverage.counts.preferences,
            artifacts=coverage.counts.artifacts,
        ),
        coverage_notes=coverage.notes,
        acquisition=AcquisitionPlan(
            status=acquisition.status,
            gaps=[
                AcquisitionGap(
                    kind=gap.kind,
                    domain=gap.domain,
                    reason=gap.reason,
                )
                for gap in acquisition.gaps
            ],
            suggestions=[
                AcquisitionSuggestion(
                    kind=suggestion.kind,
                    prompt=suggestion.prompt,
                    action=suggestion.action,
                )
                for suggestion in acquisition.suggestions
            ],
        ),
    )


@router.post("/query/feedback", response_model=QueryFeedbackResponse)
def record_feedback(
    request: Request,
    payload: QueryFeedbackRequest,
) -> QueryFeedbackResponse:
    """Persist local-only answer usefulness feedback for future calibration."""
    with get_db_connection() as conn:
        feedback_id = record_query_feedback(
            conn,
            QueryFeedbackInput(
                session_id=getattr(request.app.state.current_session, "id", None),
                query_text=payload.query,
                response_text=payload.response,
                feedback=payload.feedback,
                notes=(payload.notes or "").strip() or None,
                backend=payload.backend_used,
                query_type=payload.query_type,
                source_profile=payload.intent.source_profile,
                confidence=payload.confidence,
                intent_tags=payload.intent.intent_tags,
                domain_hints=payload.intent.domain_hints,
                domains_referenced=payload.domains_referenced,
            ),
        )
        if payload.voice_feedback is not None:
            if payload.intent.source_profile != "voice_generation":
                raise HTTPException(
                    status_code=422,
                    detail="voice_feedback is only valid for voice_generation queries.",
                )
            record_voice_feedback(
                conn,
                VoiceFeedbackInput(
                    query_feedback_id=feedback_id,
                    session_id=getattr(request.app.state.current_session, "id", None),
                    query_text=payload.query,
                    response_text=payload.response,
                    feedback=payload.voice_feedback,
                    notes=(payload.notes or "").strip() or None,
                    backend=payload.backend_used,
                    query_type=payload.query_type,
                    source_profile=payload.intent.source_profile,
                    intent_tags=payload.intent.intent_tags,
                    domains_referenced=payload.domains_referenced,
                ),
            )
            category, subject, signal, strength = _VOICE_FEEDBACK_SIGNAL_MAP[payload.voice_feedback]
            record_preference_signal(
                conn,
                PreferenceSignalInput(
                    category=category,
                    subject=subject,
                    signal=signal,
                    strength=strength,
                    source="explicit_feedback",
                ),
            )
    return QueryFeedbackResponse(id=feedback_id)


def _should_short_circuit_insufficient(context: QueryContext) -> bool:
    if getattr(context, "forced_response", None):
        return False
    if context.coverage.confidence != "insufficient_data":
        return False
    if context.backend != "local" and any(
        attr.get("routing") == "local_only" for attr in context.attributes
    ):
        # Let the privacy broker raise the routing violation instead of silently
        # returning an "insufficient data" message that hides the block.
        return False
    return True


def _contains_local_only_context(context) -> bool:
    assembled = getattr(context, "assembled_context", None)
    if assembled is not None:
        return bool(getattr(assembled, "contains_local_only", False))
    return any(attr.get("routing") == "local_only" for attr in context.attributes)


def _had_local_only_stripped(context) -> bool:
    assembled = getattr(context, "assembled_context", None)
    if assembled is not None:
        return bool(getattr(assembled, "had_local_only_stripped", False))
    return False


def _domains_used(context) -> list[str] | None:
    assembled = getattr(context, "assembled_context", None)
    if assembled is not None:
        return list(getattr(assembled, "domains_used", []))
    return None


def _provider_for_context(context, default_provider: ProviderConfig):
    return getattr(context, "provider_config", default_provider)


def _is_upstream_error(exc: Exception) -> bool:
    module_name = type(exc).__module__
    return module_name.startswith(("requests", "anthropic", "groq", "httpx"))


def _query_error_response(
    exc: Exception,
    provider_config: ProviderConfig,
) -> tuple[int, dict[str, object]]:
    if isinstance(exc, RoutingViolationError):
        logger.warning(
            "Blocked external query because it would include local_only attributes."
        )
        return (
            409,
            {
                "error": "routing_violation",
                "message": (
                    "This request was blocked to protect local-only data from being "
                    "sent to an external model."
                ),
                "privacy": blocked_privacy_state(provider_config).model_dump(mode="json"),
            },
        )

    if isinstance(exc, ConfigurationError):
        logger.warning(
            "LLM backend unavailable for query. backend_override=%s provider=%s error=%s",
            provider_config.provider,
            provider_config.provider,
            exc,
        )
        return (
            503,
            {
                "error": "backend_unavailable",
                "message": str(exc),
                "privacy": unavailable_privacy_state(provider_config).model_dump(mode="json"),
            },
        )

    if not provider_config.is_local and _is_upstream_error(exc):
        logger.exception(
            "External provider request failed. provider=%s",
            provider_config.provider,
        )
        return (
            502,
            {
                "error": "upstream_error",
                "message": (
                    f"External provider request failed for {provider_config.provider}."
                ),
                "privacy": unavailable_privacy_state(provider_config).model_dump(mode="json"),
            },
        )

    logger.exception(
        "Unhandled query failure. provider=%s",
        provider_config.provider,
    )
    return (
        500,
        {
            "error": "internal_server_error",
            "message": "internal server error",
            "privacy": unavailable_privacy_state(provider_config).model_dump(mode="json"),
        },
    )


@router.post("/query", response_model=QueryResponse)
def query(request: Request, payload: QueryRequest) -> QueryResponse | JSONResponse:
    """Return a full query response as JSON."""
    started = time.monotonic()
    provider_config = request.app.state.llm_config
    context = None
    try:
        with get_db_connection() as conn:
            provider_config = _resolve_provider(
                conn,
                request.app.state.llm_config,
                payload.backend_override,
            )
            context = prepare_query(
                payload.query,
                request.app.state.current_session,
                conn,
                provider_config,
            )
        forced_response = getattr(context, "forced_response", None)
        if forced_response is not None:
            audit = build_forced_artifact_response_decision(context, provider_config)
            record_query_result(
                request.app.state.current_session,
                context,
                forced_response,
                audit,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            return QueryResponse(
                response=forced_response,
                metadata=_metadata_from_context(
                    context,
                    duration_ms,
                    privacy_state_from_decision(audit),
                ),
            )
        if _should_short_circuit_insufficient(context):
            audit = build_insufficient_data_decision(context, _provider_for_context(context, provider_config))
            record_query_result(
                request.app.state.current_session,
                context,
                INSUFFICIENT_DATA_MESSAGE,
                audit,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            return QueryResponse(
                response=INSUFFICIENT_DATA_MESSAGE,
                metadata=_metadata_from_context(
                    context,
                    duration_ms,
                    privacy_state_from_decision(audit),
                ),
            )
        brokered = PrivacyBroker(_provider_for_context(context, provider_config)).generate_grounded_response(
            context.messages,
            attributes=context.attributes,
            retrieval_mode=context.query_type,
            contains_local_only_context=_contains_local_only_context(context),
            local_only_stripped_for_external=_had_local_only_stripped(context),
            domains_used=_domains_used(context),
        )
        result = brokered.content
        assert isinstance(result, str)
        brokered_metadata = apply_local_fallback_audit(brokered.metadata, context)
        duration_ms = int((time.monotonic() - started) * 1000)
        record_query_result(
            request.app.state.current_session,
            context,
            result,
            brokered_metadata,
        )
        return QueryResponse(
            response=result,
            metadata=_metadata_from_context(
                context,
                duration_ms,
                privacy_state_from_decision(brokered_metadata),
            ),
        )
    except Exception as exc:
        if context is not None:
            record_blocked_query(request.app.state.current_session, context, exc)
        status_code, body = _query_error_response(
            exc,
            _provider_for_context(context, provider_config) if context is not None else provider_config,
        )
        return JSONResponse(body, status_code=status_code)


@router.post("/query/stream")
def query_stream(
    request: Request,
    payload: QueryRequest,
) -> Response:
    """Stream a query response as server-sent events."""
    provider_config = request.app.state.llm_config
    context = None
    try:
        # Resolve identity context before streaming starts so the DB connection
        # closes immediately instead of staying open for the whole SSE response.
        with get_db_connection() as conn:
            provider_config = _resolve_provider(
                conn,
                request.app.state.llm_config,
                payload.backend_override,
            )
            context = prepare_query(
                payload.query,
                request.app.state.current_session,
                conn,
                provider_config,
            )
    except Exception as exc:
        status_code, body = _query_error_response(
            exc,
            _provider_for_context(context, provider_config) if context is not None else provider_config,
        )
        return JSONResponse(body, status_code=status_code)
    send_warning = payload.backend_override == "external" and _is_sensitive_query(
        payload.query,
        context.attributes,
    )

    def stream_events():
        started = time.monotonic()
        collected: list[str] = []
        try:
            yield _event(
                {
                    "type": "metadata",
                    "content": _metadata_from_context(
                        context,
                        0,
                        privacy_state_from_provider(_provider_for_context(context, provider_config)),
                    ).model_dump(mode="json"),
                }
            )
            if send_warning:
                yield _event(
                    {
                        "type": "warning",
                        "content": (
                            "Sensitive content detected. Routing to external API anyway "
                            "per your request."
                        ),
                    }
                )

            forced_response = getattr(context, "forced_response", None)
            if forced_response is not None:
                audit = build_forced_artifact_response_decision(context, provider_config)
                record_query_result(
                    request.app.state.current_session,
                    context,
                    forced_response,
                    audit,
                )
                yield _event({"type": "token", "content": forced_response})
                duration_ms = int((time.monotonic() - started) * 1000)
                yield _event(
                    {
                        "type": "metadata",
                        "content": _metadata_from_context(
                            context,
                            duration_ms,
                            privacy_state_from_decision(audit),
                        ).model_dump(mode="json"),
                    }
                )
                return

            if _should_short_circuit_insufficient(context):
                audit = build_insufficient_data_decision(context, _provider_for_context(context, provider_config))
                record_query_result(
                    request.app.state.current_session,
                    context,
                    INSUFFICIENT_DATA_MESSAGE,
                    audit,
                )
                yield _event({"type": "token", "content": INSUFFICIENT_DATA_MESSAGE})
                duration_ms = int((time.monotonic() - started) * 1000)
                yield _event(
                    {
                        "type": "metadata",
                        "content": _metadata_from_context(
                            context,
                            duration_ms,
                            privacy_state_from_decision(audit),
                        ).model_dump(mode="json"),
                    }
                )
                return

            brokered = PrivacyBroker(_provider_for_context(context, provider_config)).generate_grounded_response(
                context.messages,
                attributes=context.attributes,
                stream=True,
                retrieval_mode=context.query_type,
                contains_local_only_context=_contains_local_only_context(context),
                local_only_stripped_for_external=_had_local_only_stripped(context),
                domains_used=_domains_used(context),
            )
            response_stream = brokered.content
            assert not isinstance(response_stream, str)
            for token in response_stream:
                collected.append(token)
                yield _event({"type": "token", "content": token})

            full_response = "".join(collected)
            duration_ms = int((time.monotonic() - started) * 1000)
            brokered_metadata = apply_local_fallback_audit(brokered.metadata, context)
            record_query_result(
                request.app.state.current_session,
                context,
                full_response,
                brokered_metadata,
            )
            yield _event(
                {
                    "type": "metadata",
                    "content": _metadata_from_context(
                        context,
                        duration_ms,
                        privacy_state_from_decision(brokered_metadata),
                    ).model_dump(mode="json"),
                }
            )
        except Exception as exc:
            record_blocked_query(request.app.state.current_session, context, exc)
            _, body = _query_error_response(
                exc,
                _provider_for_context(context, provider_config),
            )
            yield _event(
                {
                    "type": "error",
                    "content": body["message"],
                    "code": body["error"],
                    "privacy": body.get("privacy"),
                }
            )
        finally:
            yield _event({"type": "done"})

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
