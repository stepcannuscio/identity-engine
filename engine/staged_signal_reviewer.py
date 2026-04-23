"""Helpers for reviewing passive-learning session signals in Teach."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from typing import Any

from db.preference_signals import PreferenceSignalInput, record_preference_signal
from engine.capture import save_preview_attributes

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class StagedSessionSignal:
    """One staged passive-learning signal awaiting review."""

    id: str
    session_id: str
    exchange_index: int
    signal_type: str
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class ReviewActionResult:
    """Summary of accepting or dismissing one staged signal."""

    signal_id: str
    status: str
    attributes_saved: int = 0
    preference_signals_saved: int = 0


def _slug(value: str) -> str:
    normalized = _NON_WORD_RE.sub("_", value.lower()).strip("_")
    return normalized or "signal"


def _to_float(value: object, default: float) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: object, default: int) -> int:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_created_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _signal_from_row(row) -> StagedSessionSignal:
    payload = json.loads(str(row[4]))
    if not isinstance(payload, dict):
        payload = {}
    return StagedSessionSignal(
        id=str(row[0]),
        session_id=str(row[1]),
        exchange_index=int(row[2]),
        signal_type=str(row[3]),
        payload=payload,
        created_at=_parse_created_at(row[5]),
    )


def list_pending_signals(conn, *, limit: int = 20) -> list[StagedSessionSignal]:
    """Return unprocessed staged conversation signals."""
    rows = conn.execute(
        """
        SELECT id, session_id, exchange_index, signal_type, payload_json, created_at
        FROM extracted_session_signals
        WHERE processed = 0
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_signal_from_row(row) for row in rows]


def count_pending_signals(conn) -> int:
    """Return the number of unprocessed staged conversation signals."""
    row = conn.execute(
        "SELECT COUNT(*) FROM extracted_session_signals WHERE processed = 0"
    ).fetchone()
    return int(row[0]) if row is not None else 0


def get_signal(conn, signal_id: str) -> StagedSessionSignal | None:
    """Return one staged signal by id, if it exists."""
    row = conn.execute(
        """
        SELECT id, session_id, exchange_index, signal_type, payload_json, created_at
        FROM extracted_session_signals
        WHERE id = ?
        """,
        (signal_id,),
    ).fetchone()
    if row is None:
        return None
    return _signal_from_row(row)


def _mark_processed(conn, signal_id: str) -> None:
    conn.execute(
        "UPDATE extracted_session_signals SET processed = 1 WHERE id = ?",
        (signal_id,),
    )
    conn.commit()


def _accept_attribute_candidate(conn, signal: StagedSessionSignal) -> ReviewActionResult:
    saved = save_preview_attributes(
        conn,
        [
            {
                "domain": str(signal.payload["domain"]),
                "label": str(signal.payload["label"]),
                "value": str(signal.payload["value"]),
                "elaboration": signal.payload.get("elaboration"),
                "mutability": str(signal.payload.get("mutability", "evolving")),
                "confidence": _to_float(signal.payload.get("confidence", 0.55) or 0.55, 0.55),
            }
        ],
    )
    _mark_processed(conn, signal.id)
    return ReviewActionResult(
        signal_id=signal.id,
        status="accepted",
        attributes_saved=len(saved),
        preference_signals_saved=0,
    )


def _accept_preference(conn, signal: StagedSessionSignal) -> ReviewActionResult:
    record_preference_signal(
        conn,
        PreferenceSignalInput(
            category=str(signal.payload["category"]),
            subject=str(signal.payload["subject"]),
            signal=str(signal.payload["signal"]),
            strength=_to_int(signal.payload.get("strength", 3) or 3, 3),
            source="system_inference",
            context={
                "source_profile": str(signal.payload.get("source_profile", "unknown")),
                "query_excerpt": str(signal.payload.get("query_excerpt", "")),
                "summary": str(signal.payload.get("summary", "")),
            },
        ),
    )
    _mark_processed(conn, signal.id)
    return ReviewActionResult(
        signal_id=signal.id,
        status="accepted",
        attributes_saved=0,
        preference_signals_saved=1,
    )


def _accept_correction(conn, signal: StagedSessionSignal) -> ReviewActionResult:
    raw_attribute_ids = signal.payload.get("attribute_ids", [])
    attribute_ids = [
        str(attribute_id)
        for attribute_id in raw_attribute_ids
        if str(attribute_id).strip()
    ] if isinstance(raw_attribute_ids, list) else []
    saved_count = 0
    subject_root = _slug(str(signal.payload.get("summary", "conversation_correction")))

    if attribute_ids:
        rows = conn.execute(
            """
            SELECT id, label
            FROM attributes
            WHERE id IN ({placeholders})
            """.format(placeholders=", ".join("?" for _ in attribute_ids)),
            attribute_ids,
        ).fetchall()
        labels_by_id = {str(row[0]): str(row[1]) for row in rows}
    else:
        labels_by_id = {}

    for attribute_id in attribute_ids or [None]:
        subject = subject_root
        if attribute_id is not None:
            subject = _slug(labels_by_id.get(attribute_id, subject_root))
        record_preference_signal(
            conn,
            PreferenceSignalInput(
                category="retrieval_correction",
                subject=subject,
                signal="reject",
                strength=4,
                source="correction",
                context={
                    "source_profile": str(signal.payload.get("source_profile", "unknown")),
                    "query_excerpt": str(signal.payload.get("query_excerpt", "")),
                    "correction_text": str(signal.payload.get("correction_text", "")),
                },
                attribute_id=attribute_id,
            ),
        )
        saved_count += 1

    _mark_processed(conn, signal.id)
    return ReviewActionResult(
        signal_id=signal.id,
        status="accepted",
        attributes_saved=0,
        preference_signals_saved=saved_count,
    )


def accept_signal(conn, signal_id: str) -> ReviewActionResult:
    """Accept one staged signal and promote it into the appropriate store."""
    signal = get_signal(conn, signal_id)
    if signal is None:
        raise ValueError("staged session signal not found")
    row = conn.execute(
        "SELECT processed FROM extracted_session_signals WHERE id = ?",
        (signal_id,),
    ).fetchone()
    if row is None:
        raise ValueError("staged session signal not found")
    if int(row[0]) == 1:
        raise ValueError("staged session signal has already been processed")

    if signal.signal_type == "attribute_candidate":
        return _accept_attribute_candidate(conn, signal)
    if signal.signal_type == "preference":
        return _accept_preference(conn, signal)
    if signal.signal_type == "correction":
        return _accept_correction(conn, signal)
    raise ValueError("unsupported staged session signal type")


def dismiss_signal(conn, signal_id: str) -> ReviewActionResult:
    """Dismiss one staged signal without promoting it."""
    signal = get_signal(conn, signal_id)
    if signal is None:
        raise ValueError("staged session signal not found")
    _mark_processed(conn, signal_id)
    return ReviewActionResult(signal_id=signal_id, status="dismissed")
