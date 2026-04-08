"""Query API routes, including streaming server-sent events."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from config.llm_router import (
    ConfigurationError,
    ProviderConfig,
    generate_response,
    resolve_external_router,
    resolve_local_router,
)
from engine.query_engine import prepare_query, record_query_result
from server.db import get_db_connection
from server.models.schemas import QueryMetadata, QueryRequest, QueryResponse

router = APIRouter(tags=["query"])

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


def _metadata_from_context(context, duration_ms: int) -> QueryMetadata:
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
    )


@router.post("/query", response_model=QueryResponse)
def query(request: Request, payload: QueryRequest) -> QueryResponse:
    """Return a full query response as JSON."""
    provider_config = _resolve_provider(
        request.app.state.llm_config,
        payload.backend_override,
    )
    started = time.monotonic()
    with get_db_connection() as conn:
        context = prepare_query(
            payload.query,
            request.app.state.current_session,
            conn,
            provider_config,
        )
    result = generate_response(context.messages, provider_config)
    assert isinstance(result, str)
    duration_ms = int((time.monotonic() - started) * 1000)
    record_query_result(request.app.state.current_session, context, result)
    return QueryResponse(
        response=result,
        metadata=_metadata_from_context(context, duration_ms),
    )


@router.post("/query/stream")
def query_stream(request: Request, payload: QueryRequest) -> StreamingResponse:
    """Stream a query response as server-sent events."""
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
    send_warning = payload.backend_override == "external" and _is_sensitive_query(
        payload.query,
        context.attributes,
    )

    def stream_events():
        started = time.monotonic()
        collected: list[str] = []
        try:
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

            response_stream = generate_response(context.messages, provider_config, stream=True)
            assert not isinstance(response_stream, str)
            for token in response_stream:
                collected.append(token)
                yield _event({"type": "token", "content": token})

            full_response = "".join(collected)
            duration_ms = int((time.monotonic() - started) * 1000)
            record_query_result(request.app.state.current_session, context, full_response)
            yield _event(
                {
                    "type": "metadata",
                    "content": _metadata_from_context(context, duration_ms).model_dump(mode="json"),
                }
            )
        except ConfigurationError:
            yield _event({"type": "error", "content": "internal server error"})
        except Exception:
            yield _event({"type": "error", "content": "internal server error"})
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
