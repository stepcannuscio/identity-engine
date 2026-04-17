"""Authenticated API routes for lightweight preference-signal storage."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from db.preference_signals import (
    PreferenceSignalInput,
    list_preference_signals,
    record_preference_signal,
    summarize_preference_signals,
)
from server.db import get_db_connection
from server.models.schemas import (
    PreferenceSignalCreateRequest,
    PreferenceSignalResponse,
    PreferenceSignalSummaryResponse,
)

router = APIRouter(tags=["preferences"])


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _serialize_preference_signal(record) -> PreferenceSignalResponse:
    return PreferenceSignalResponse(
        id=record.id,
        category=record.category,
        subject=record.subject,
        signal=record.signal,
        strength=record.strength,
        source=record.source,
        context=record.context,
        attribute_id=record.attribute_id,
        created_at=_parse_timestamp(record.created_at),
    )


@router.post("/preferences/signals", response_model=PreferenceSignalResponse)
def create_preference_signal(
    payload: PreferenceSignalCreateRequest,
    request: Request,
) -> PreferenceSignalResponse:
    """Persist one explicit preference signal."""
    _ = request
    try:
        with get_db_connection() as conn:
            record = record_preference_signal(
                conn,
                PreferenceSignalInput(
                    category=payload.category,
                    subject=payload.subject,
                    signal=payload.signal,
                    strength=payload.strength,
                    source=payload.source,
                    context=payload.context,
                    attribute_id=payload.attribute_id,
                ),
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _serialize_preference_signal(record)


@router.get("/preferences/signals", response_model=list[PreferenceSignalResponse])
def get_preference_signals(
    request: Request,
    category: str | None = None,
    subject: str | None = None,
) -> list[PreferenceSignalResponse]:
    """List stored preference signals with optional category/subject filters."""
    _ = request
    with get_db_connection() as conn:
        rows = list_preference_signals(conn, category=category, subject=subject)
    return [_serialize_preference_signal(row) for row in rows]


@router.get(
    "/preferences/signals/summary",
    response_model=list[PreferenceSignalSummaryResponse],
)
def get_preference_signal_summary(
    request: Request,
    category: str | None = None,
    subject: str | None = None,
) -> list[PreferenceSignalSummaryResponse]:
    """Return deterministic preference summaries for future ranking/planning."""
    _ = request
    with get_db_connection() as conn:
        rows = summarize_preference_signals(conn, category=category, subject=subject)
    return [
        PreferenceSignalSummaryResponse(
            category=row.category,
            subject=row.subject,
            observations=row.observations,
            positive_count=row.positive_count,
            negative_count=row.negative_count,
            net_score=row.net_score,
            latest_at=_parse_timestamp(row.latest_at),
        )
        for row in rows
    ]
