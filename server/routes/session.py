"""Reflection-session history routes for the FastAPI server."""

from __future__ import annotations

from collections.abc import Mapping
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.db import get_db_connection
from server.models.schemas import CurrentSessionStatus, RoutingLogEntry, SessionRecord
from server.privacy import privacy_state_from_routing_log, session_privacy_state

router = APIRouter(tags=["sessions"])


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("Invalid routing-log timestamp.")


def _backend_from_entry(entry: Mapping[str, Any]) -> str:
    if entry.get("is_local") is True:
        return "local"
    provider = entry.get("provider")
    if provider:
        return str(provider)
    return str(entry.get("backend", "local"))


def _serialize_session(row) -> SessionRecord:
    raw_log = row[8] or "[]"
    parsed_log = json.loads(raw_log)
    safe_entries: list[Mapping[str, Any]] = [
        entry for entry in parsed_log if isinstance(entry, dict)
    ]
    routing_log = [
        RoutingLogEntry(
            query=str(entry.get("query", "")),
            query_type=str(entry.get("query_type", entry.get("retrieval_mode", ""))),
            backend=_backend_from_entry(entry),
            attribute_count=int(entry.get("attribute_count", 0)),
            domains_referenced=list(entry.get("domains_referenced", [])),
            timestamp=_parse_timestamp(entry.get("timestamp")),
            task_type=entry.get("task_type"),
            provider=entry.get("provider"),
            model=entry.get("model"),
            is_local=entry.get("is_local"),
            routing_enforced=entry.get("routing_enforced"),
            contains_local_only_context=entry.get("contains_local_only_context"),
            blocked_external_attributes_count=int(
                entry.get("blocked_external_attributes_count", 0)
            ),
            retrieval_mode=entry.get("retrieval_mode"),
            decision=entry.get("decision"),
            warning=None,
            reason=None,
            privacy=privacy_state_from_routing_log(entry),
        )
        for entry in safe_entries
    ]
    return SessionRecord(
        id=str(row[0]),
        session_type=str(row[1]),
        summary=row[2],
        attributes_created=int(row[3]),
        attributes_updated=int(row[4]),
        external_calls_made=int(row[5]),
        started_at=row[6],
        ended_at=row[7],
        routing_log=routing_log,
        privacy=session_privacy_state(safe_entries),
    )


@router.get("/sessions", response_model=list[SessionRecord])
def list_sessions(request: Request) -> list[SessionRecord]:
    """List recent reflection sessions."""
    _ = request
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                session_type,
                summary,
                attributes_created,
                attributes_updated,
                external_calls_made,
                started_at,
                ended_at,
                routing_log
            FROM reflection_sessions
            ORDER BY started_at DESC
            LIMIT 20
            """
        ).fetchall()
    return [_serialize_session(row) for row in rows]


@router.get("/sessions/current", response_model=CurrentSessionStatus)
def current_session(request: Request) -> CurrentSessionStatus:
    """Return live stats for the in-memory query session."""
    session = request.app.state.current_session
    backend = "local"
    if session.routing_log:
        backend = _backend_from_entry(session.routing_log[-1])
    return CurrentSessionStatus(
        id=session.id,
        query_count=session.query_count,
        attributes_retrieved=session.attributes_retrieved,
        backend=backend,
        started_at=session.started_at,
    )


@router.get("/sessions/{session_id}", response_model=SessionRecord)
def get_session(session_id: str, request: Request) -> SessionRecord:
    """Return one stored reflection session."""
    _ = request
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                session_type,
                summary,
                attributes_created,
                attributes_updated,
                external_calls_made,
                started_at,
                ended_at,
                routing_log
            FROM reflection_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _serialize_session(row)
