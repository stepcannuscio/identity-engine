"""Reflection-session history routes for the FastAPI server."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from server.db import get_db_connection
from server.models.schemas import CurrentSessionStatus, RoutingLogEntry, SessionRecord

router = APIRouter(tags=["sessions"])


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("Invalid routing-log timestamp.")


def _serialize_session(row) -> SessionRecord:
    raw_log = row[8] or "[]"
    parsed_log = json.loads(raw_log)
    routing_log = [
        RoutingLogEntry(
            query=str(entry.get("query", "")),
            query_type=str(entry.get("query_type", "")),
            backend=str(entry.get("backend", "local")),
            attribute_count=int(entry.get("attribute_count", 0)),
            domains_referenced=list(entry.get("domains_referenced", [])),
            timestamp=_parse_timestamp(entry.get("timestamp")),
        )
        for entry in parsed_log
        if isinstance(entry, dict)
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
        backend = str(session.routing_log[-1]["backend"])
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
