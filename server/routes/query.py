"""Query API routes, including streaming server-sent events."""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from config.llm_router import (
    ConfigurationError,
    ProviderConfig,
    resolve_external_router,
    resolve_local_router,
)
from engine.privacy_broker import PrivacyBroker
from engine.prompt_builder import RoutingViolationError
from engine.query_engine import prepare_query, record_blocked_query, record_query_result
from server.db import get_db_connection
from server.models.schemas import QueryMetadata, QueryRequest, QueryResponse
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


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _resolve_provider(default_config: ProviderConfig, override: str | None) -> ProviderConfig:
    if override is None:
        return default_config
    if override == "local":
        return default_config if default_config.is_local else resolve_local_router()
    if override == "external":
        return default_config if not default_config.is_local else resolve_external_router()
    raise HTTPException(status_code=400, detail="invalid backend_override")


def _is_sensitive_query(query_text: str, attributes: list[dict]) -> bool:
    lowered = query_text.lower()
    if any(term in lowered for term in _SENSITIVE_TERMS):
        return True
    return any(str(attr.get("domain")) in _SENSITIVE_DOMAINS for attr in attributes)


def _metadata_from_context(context, duration_ms: int, privacy) -> QueryMetadata:
    domains = sorted(
        {
            str(attr.get("domain", ""))
            for attr in context.attributes
            if attr.get("domain")
        }
    )
    return QueryMetadata(
        query_type=context.query_type,
        attributes_used=len(context.attributes),
        backend_used=context.backend,
        domains_referenced=domains,
        duration_ms=duration_ms,
        privacy=privacy,
    )


def _is_upstream_error(exc: Exception) -> bool:
    module_name = type(exc).__module__
    return module_name.startswith(("requests", "anthropic", "groq", "httpx"))


def _query_error_response(
    exc: Exception,
    provider_config: ProviderConfig,
    query_text: str,
) -> tuple[int, dict[str, object]]:
    if isinstance(exc, RoutingViolationError):
        logger.warning(
            "Blocked external query because it would include local_only attributes. query=%r",
            query_text,
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
            "External provider request failed. provider=%s query=%r",
            provider_config.provider,
            query_text,
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
        "Unhandled query failure. provider=%s query=%r",
        provider_config.provider,
        query_text,
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
        provider_config = _resolve_provider(
            request.app.state.llm_config,
            payload.backend_override,
        )
        with get_db_connection() as conn:
            context = prepare_query(
                payload.query,
                request.app.state.current_session,
                conn,
                provider_config,
            )
        brokered = PrivacyBroker(provider_config).generate_grounded_response(
            context.messages,
            attributes=context.attributes,
            retrieval_mode=context.query_type,
        )
        result = brokered.content
        assert isinstance(result, str)
        duration_ms = int((time.monotonic() - started) * 1000)
        record_query_result(
            request.app.state.current_session,
            context,
            result,
            brokered.metadata,
        )
        return QueryResponse(
            response=result,
            metadata=_metadata_from_context(
                context,
                duration_ms,
                privacy_state_from_decision(brokered.metadata),
            ),
        )
    except Exception as exc:
        if context is not None:
            record_blocked_query(request.app.state.current_session, context, exc)
        status_code, body = _query_error_response(exc, provider_config, payload.query)
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
        provider_config = _resolve_provider(
            request.app.state.llm_config,
            payload.backend_override,
        )
        # Resolve identity context before streaming starts so the DB connection
        # closes immediately instead of staying open for the whole SSE response.
        with get_db_connection() as conn:
            context = prepare_query(
                payload.query,
                request.app.state.current_session,
                conn,
                provider_config,
            )
    except Exception as exc:
        status_code, body = _query_error_response(exc, provider_config, payload.query)
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
                        privacy_state_from_provider(provider_config),
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

            brokered = PrivacyBroker(provider_config).generate_grounded_response(
                context.messages,
                attributes=context.attributes,
                stream=True,
                retrieval_mode=context.query_type,
            )
            response_stream = brokered.content
            assert not isinstance(response_stream, str)
            for token in response_stream:
                collected.append(token)
                yield _event({"type": "token", "content": token})

            full_response = "".join(collected)
            duration_ms = int((time.monotonic() - started) * 1000)
            record_query_result(
                request.app.state.current_session,
                context,
                full_response,
                brokered.metadata,
            )
            yield _event(
                {
                    "type": "metadata",
                    "content": _metadata_from_context(
                        context,
                        duration_ms,
                        privacy_state_from_decision(brokered.metadata),
                    ).model_dump(mode="json"),
                }
            )
        except Exception as exc:
            record_blocked_query(request.app.state.current_session, context, exc)
            _, body = _query_error_response(exc, provider_config, payload.query)
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
