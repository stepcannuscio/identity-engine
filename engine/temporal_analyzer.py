"""Temporal identity analysis — detects drift, shift clusters, and confidence decay."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
import uuid

_DRIFT_WINDOW_DAYS = 365
_DRIFT_MIN_CHANGES = 2
_SHIFT_WINDOW_DAYS = 90
_SHIFT_MIN_ATTRIBUTES = 3
_DECAY_DAYS = 540
_DECAY_MIN_CONFIDENCE = 0.70


@dataclass(frozen=True)
class TemporalEvent:
    """One detected temporal identity event staged for Teach review."""

    id: str
    event_type: str
    domain: str
    attribute_ids: list[str]
    detected_at: datetime
    description: str | None
    status: str


def _parse_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_list(value: object) -> list[str]:
    if value in {None, ""}:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _canonical_json(items: list[str]) -> str:
    return json.dumps(sorted(set(items)), separators=(",", ":"))


def _now_str() -> str:
    return datetime.now(UTC).isoformat()


def _existing_active_events(conn) -> tuple[set[str], set[str], set[str]]:
    """Return sets of existing active (drift attr_ids, shift domains, decay attr_ids)."""
    rows = conn.execute(
        "SELECT event_type, domain, attribute_ids_json FROM temporal_events WHERE status = 'active'"
    ).fetchall()
    drift_attrs: set[str] = set()
    shift_domains: set[str] = set()
    decay_attrs: set[str] = set()
    for row in rows:
        event_type = str(row[0])
        domain = str(row[1])
        attr_ids = _json_list(row[2])
        if event_type == "drift":
            drift_attrs.update(attr_ids)
        elif event_type == "shift_cluster":
            shift_domains.add(domain)
        elif event_type == "confidence_decay":
            decay_attrs.update(attr_ids)
    return drift_attrs, shift_domains, decay_attrs


def _detect_drift(conn, existing_drift_attrs: set[str]) -> int:
    """Detect attributes changed 2+ times in the last 365 days and stage drift events."""
    cutoff = (datetime.now(UTC) - timedelta(days=_DRIFT_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT h.attribute_id, d.name, a.label, COUNT(h.id) AS change_count
        FROM attribute_history h
        JOIN attributes a ON a.id = h.attribute_id
        JOIN domains d ON d.id = a.domain_id
        WHERE h.changed_at >= ?
          AND a.status IN ('active', 'confirmed')
        GROUP BY h.attribute_id
        HAVING change_count >= ?
        """,
        (cutoff, _DRIFT_MIN_CHANGES),
    ).fetchall()

    inserted = 0
    now = _now_str()
    for row in rows:
        attribute_id = str(row[0])
        if attribute_id in existing_drift_attrs:
            continue
        domain = str(row[1])
        label = str(row[2])
        change_count = int(row[3])
        description = (
            f"'{label}' has changed {change_count} times in the last year, "
            "suggesting it may be evolving."
        )
        conn.execute(
            """
            INSERT INTO temporal_events
                (id, event_type, domain, attribute_ids_json, detected_at, description, status)
            VALUES (?, 'drift', ?, ?, ?, ?, 'active')
            """,
            (str(uuid.uuid4()), domain, _canonical_json([attribute_id]), now, description),
        )
        existing_drift_attrs.add(attribute_id)
        inserted += 1
    return inserted


def _detect_shift_clusters(conn, existing_shift_domains: set[str]) -> int:
    """Detect domains with 3+ attribute changes within any 90-day window."""
    cutoff = (datetime.now(UTC) - timedelta(days=_DRIFT_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT h.attribute_id, d.name, h.changed_at
        FROM attribute_history h
        JOIN attributes a ON a.id = h.attribute_id
        JOIN domains d ON d.id = a.domain_id
        WHERE h.changed_at >= ?
        ORDER BY d.name, h.changed_at
        """,
        (cutoff,),
    ).fetchall()

    domain_changes: dict[str, list[tuple[datetime, str]]] = {}
    for row in rows:
        attribute_id = str(row[0])
        domain = str(row[1])
        changed_at = _parse_timestamp(row[2])
        domain_changes.setdefault(domain, []).append((changed_at, attribute_id))

    inserted = 0
    now = _now_str()
    window = timedelta(days=_SHIFT_WINDOW_DAYS)

    for domain, changes in domain_changes.items():
        if domain in existing_shift_domains:
            continue
        changes.sort(key=lambda t: t[0])
        for i in range(len(changes)):
            window_start = changes[i][0]
            window_end = window_start + window
            attrs_in_window = {
                attr_id for ts, attr_id in changes if window_start <= ts <= window_end
            }
            if len(attrs_in_window) >= _SHIFT_MIN_ATTRIBUTES:
                attr_ids_json = _canonical_json(sorted(attrs_in_window))
                description = (
                    f"{len(attrs_in_window)} attributes in '{domain}' changed "
                    "within a 90-day window, suggesting a possible life transition."
                )
                conn.execute(
                    """
                    INSERT INTO temporal_events
                        (id, event_type, domain, attribute_ids_json, detected_at, description, status)
                    VALUES (?, 'shift_cluster', ?, ?, ?, ?, 'active')
                    """,
                    (str(uuid.uuid4()), domain, attr_ids_json, now, description),
                )
                existing_shift_domains.add(domain)
                inserted += 1
                break
    return inserted


def _detect_confidence_decay(conn, existing_decay_attrs: set[str]) -> int:
    """Detect high-confidence attributes not confirmed or updated in 540+ days."""
    cutoff = (datetime.now(UTC) - timedelta(days=_DECAY_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT a.id, d.name, a.label, a.value, a.confidence
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE a.status IN ('active', 'confirmed')
          AND a.confidence >= ?
          AND (a.last_confirmed IS NULL OR a.last_confirmed <= ?)
          AND (a.updated_at IS NULL OR a.updated_at <= ?)
        """,
        (_DECAY_MIN_CONFIDENCE, cutoff, cutoff),
    ).fetchall()

    inserted = 0
    now = _now_str()
    for row in rows:
        attribute_id = str(row[0])
        if attribute_id in existing_decay_attrs:
            continue
        domain = str(row[1])
        label = str(row[2])
        value = str(row[3])
        confidence = float(row[4])
        description = (
            f"'{label}' (confidence {confidence:.2f}) has not been confirmed "
            f"in over {_DECAY_DAYS} days. Is this still true: \"{value}\"?"
        )
        conn.execute(
            """
            INSERT INTO temporal_events
                (id, event_type, domain, attribute_ids_json, detected_at, description, status)
            VALUES (?, 'confidence_decay', ?, ?, ?, ?, 'active')
            """,
            (str(uuid.uuid4()), domain, _canonical_json([attribute_id]), now, description),
        )
        existing_decay_attrs.add(attribute_id)
        inserted += 1
    return inserted


def _auto_resolve_stale_decay_events(conn) -> None:
    """Resolve confidence_decay events for attributes that have since been confirmed."""
    rows = conn.execute(
        """
        SELECT id, attribute_ids_json
        FROM temporal_events
        WHERE event_type = 'confidence_decay' AND status = 'active'
        """
    ).fetchall()
    if not rows:
        return
    cutoff = (datetime.now(UTC) - timedelta(days=_DECAY_DAYS)).isoformat()
    for row in rows:
        event_id = str(row[0])
        attr_ids = _json_list(row[1])
        if not attr_ids:
            continue
        attribute_id = attr_ids[0]
        attr_row = conn.execute(
            "SELECT status, last_confirmed, updated_at FROM attributes WHERE id = ?",
            (attribute_id,),
        ).fetchone()
        if attr_row is None:
            conn.execute(
                "UPDATE temporal_events SET status = 'resolved' WHERE id = ?",
                (event_id,),
            )
            continue
        status = str(attr_row[0])
        if status not in ("active", "confirmed"):
            conn.execute(
                "UPDATE temporal_events SET status = 'resolved' WHERE id = ?",
                (event_id,),
            )
            continue
        last_confirmed = attr_row[1]
        updated_at = attr_row[2]
        if (last_confirmed and str(last_confirmed) > cutoff) or (
            updated_at and str(updated_at) > cutoff
        ):
            conn.execute(
                "UPDATE temporal_events SET status = 'resolved' WHERE id = ?",
                (event_id,),
            )


def refresh_temporal_intelligence(conn) -> list[TemporalEvent]:
    """Detect and stage temporal events; auto-resolve stale decay events first."""
    _auto_resolve_stale_decay_events(conn)
    drift_attrs, shift_domains, decay_attrs = _existing_active_events(conn)
    inserted = 0
    inserted += _detect_drift(conn, drift_attrs)
    inserted += _detect_shift_clusters(conn, shift_domains)
    inserted += _detect_confidence_decay(conn, decay_attrs)
    if inserted:
        conn.commit()
    return list_active_temporal_events(conn)


def list_active_temporal_events(
    conn,
    *,
    event_type: str | None = None,
    domain: str | None = None,
) -> list[TemporalEvent]:
    """Return active temporal events, optionally filtered."""
    params: list[Any] = []
    where = "WHERE status = 'active'"
    if event_type:
        where += " AND event_type = ?"
        params.append(event_type)
    if domain:
        where += " AND domain = ?"
        params.append(domain)
    rows = conn.execute(
        f"""
        SELECT id, event_type, domain, attribute_ids_json, detected_at, description, status
        FROM temporal_events
        {where}
        ORDER BY detected_at DESC, id DESC
        """,
        params,
    ).fetchall()
    return [
        TemporalEvent(
            id=str(row[0]),
            event_type=str(row[1]),
            domain=str(row[2]),
            attribute_ids=_json_list(row[3]),
            detected_at=_parse_timestamp(row[4]),
            description=str(row[5]) if row[5] is not None else None,
            status=str(row[6]),
        )
        for row in rows
    ]


def list_all_temporal_events(conn) -> list[TemporalEvent]:
    """Return all temporal events (active and resolved) for the evolution timeline."""
    rows = conn.execute(
        """
        SELECT id, event_type, domain, attribute_ids_json, detected_at, description, status
        FROM temporal_events
        ORDER BY detected_at DESC, id DESC
        """
    ).fetchall()
    return [
        TemporalEvent(
            id=str(row[0]),
            event_type=str(row[1]),
            domain=str(row[2]),
            attribute_ids=_json_list(row[3]),
            detected_at=_parse_timestamp(row[4]),
            description=str(row[5]) if row[5] is not None else None,
            status=str(row[6]),
        )
        for row in rows
    ]
