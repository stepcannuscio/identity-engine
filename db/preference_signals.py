"""Helpers for storing and reading lightweight preference-signal records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import uuid

_ALLOWED_SIGNALS = {"like", "dislike", "accept", "reject", "prefer", "avoid"}
_ALLOWED_SOURCES = {
    "explicit_feedback",
    "behavior",
    "correction",
    "system_inference",
}
_POSITIVE_SIGNALS = {"like", "accept", "prefer"}
_NEGATIVE_SIGNALS = {"dislike", "reject", "avoid"}


@dataclass(frozen=True, slots=True)
class PreferenceSignalInput:
    """Typed input for one preference-signal row."""

    category: str
    subject: str
    signal: str
    strength: int = 3
    source: str = "explicit_feedback"
    context: dict[str, str | int | float | bool] | None = None
    attribute_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreferenceSignalRecord:
    """Stored preference-signal row."""

    id: str
    category: str
    subject: str
    signal: str
    strength: int
    source: str
    context: dict[str, str | int | float | bool] | None
    attribute_id: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class PreferenceSignalSummary:
    """Simple deterministic aggregate for one preference subject."""

    category: str
    subject: str
    observations: int
    positive_count: int
    negative_count: int
    net_score: int
    latest_at: str


@dataclass(slots=True)
class _PreferenceSignalSummaryAccumulator:
    """Mutable typed bucket used while aggregating preference summaries."""

    observations: int
    positive_count: int
    negative_count: int
    net_score: int
    latest_at: str


def _validate_attribute_reference(conn, attribute_id: str | None) -> None:
    if not attribute_id:
        return
    row = conn.execute(
        "SELECT id FROM attributes WHERE id = ?",
        (attribute_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Preference signal attribute_id does not reference an attribute.")


def _normalize_signal_input(item: PreferenceSignalInput) -> PreferenceSignalInput:
    category = item.category.strip()
    if not category:
        raise ValueError("Preference signal category is required.")

    subject = item.subject.strip()
    if not subject:
        raise ValueError("Preference signal subject is required.")

    signal = item.signal.strip().lower()
    if signal not in _ALLOWED_SIGNALS:
        raise ValueError("Preference signal type is invalid.")

    source = item.source.strip().lower()
    if source not in _ALLOWED_SOURCES:
        raise ValueError("Preference signal source is invalid.")

    strength = int(item.strength)
    if not 1 <= strength <= 5:
        raise ValueError("Preference signal strength must be between 1 and 5.")

    return PreferenceSignalInput(
        category=category,
        subject=subject,
        signal=signal,
        strength=strength,
        source=source,
        context=item.context,
        attribute_id=item.attribute_id,
    )


def _serialize_context(context: dict[str, str | int | float | bool] | None) -> str | None:
    if context is None:
        return None
    return json.dumps(context, sort_keys=True)


def _deserialize_context(context_json: str | None) -> dict[str, str | int | float | bool] | None:
    if not context_json:
        return None
    value = json.loads(context_json)
    if isinstance(value, dict):
        return value
    return None


def _record_from_row(row) -> PreferenceSignalRecord:
    return PreferenceSignalRecord(
        id=str(row[0]),
        category=str(row[1]),
        subject=str(row[2]),
        signal=str(row[3]),
        strength=int(row[4]),
        source=str(row[5]),
        context=_deserialize_context(row[6]),
        attribute_id=row[7],
        created_at=str(row[8]),
    )


def record_preference_signal(
    conn,
    signal_input: PreferenceSignalInput,
) -> PreferenceSignalRecord:
    """Insert one explicit preference signal and return the stored record."""
    normalized = _normalize_signal_input(signal_input)
    _validate_attribute_reference(conn, normalized.attribute_id)

    record = PreferenceSignalRecord(
        id=str(uuid.uuid4()),
        category=normalized.category,
        subject=normalized.subject,
        signal=normalized.signal,
        strength=normalized.strength,
        source=normalized.source,
        context=normalized.context,
        attribute_id=normalized.attribute_id,
        created_at=datetime.now(UTC).isoformat(),
    )

    conn.execute(
        """
        INSERT INTO preference_signals (
            id,
            category,
            subject,
            signal,
            strength,
            source,
            context_json,
            attribute_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.id,
            record.category,
            record.subject,
            record.signal,
            record.strength,
            record.source,
            _serialize_context(record.context),
            record.attribute_id,
            record.created_at,
        ),
    )
    conn.commit()
    return record


def list_preference_signals(
    conn,
    *,
    category: str | None = None,
    subject: str | None = None,
) -> list[PreferenceSignalRecord]:
    """Return stored preference signals ordered from newest to oldest."""
    clauses: list[str] = []
    params: list[object] = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if subject:
        clauses.append("subject = ?")
        params.append(subject)

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    rows = conn.execute(
        f"""
        SELECT
            id,
            category,
            subject,
            signal,
            strength,
            source,
            context_json,
            attribute_id,
            created_at
        FROM preference_signals
        {where}
        ORDER BY created_at DESC, id DESC
        """,
        tuple(params),
    ).fetchall()
    return [_record_from_row(row) for row in rows]


def summarize_preference_signals(
    conn,
    *,
    category: str | None = None,
    subject: str | None = None,
) -> list[PreferenceSignalSummary]:
    """Summarize preference signals by category and subject."""
    rows = list_preference_signals(conn, category=category, subject=subject)
    grouped: dict[tuple[str, str], _PreferenceSignalSummaryAccumulator] = {}

    for row in rows:
        key = (row.category, row.subject)
        bucket = grouped.setdefault(
            key,
            _PreferenceSignalSummaryAccumulator(
                observations=0,
                positive_count=0,
                negative_count=0,
                net_score=0,
                latest_at=row.created_at,
            ),
        )
        bucket.observations += 1
        if row.signal in _POSITIVE_SIGNALS:
            bucket.positive_count += 1
            bucket.net_score += row.strength
        elif row.signal in _NEGATIVE_SIGNALS:
            bucket.negative_count += 1
            bucket.net_score -= row.strength

    summaries = [
        PreferenceSignalSummary(
            category=category_name,
            subject=subject_name,
            observations=bucket.observations,
            positive_count=bucket.positive_count,
            negative_count=bucket.negative_count,
            net_score=bucket.net_score,
            latest_at=bucket.latest_at,
        )
        for (category_name, subject_name), bucket in grouped.items()
    ]
    summaries.sort(key=lambda item: (item.category, item.subject))
    return summaries
