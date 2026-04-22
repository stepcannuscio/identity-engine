"""Detect high-confidence tensions across active identity attributes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import uuid

from engine.text_utils import tokenize

_MIN_CONFIDENCE = 0.7
_ACTIVE_STATUSES = {"active", "confirmed"}
_TEXT_FIELDS = ("label", "value", "elaboration")

_POLARITY_AXES: dict[str, dict[str, frozenset[str]]] = {
    "connection_isolation": {
        "connection": frozenset(
            {
                "connected",
                "collaborative",
                "close",
                "community",
                "intimate",
                "open",
                "social",
                "trusting",
                "team",
            }
        ),
        "isolation": frozenset(
            {
                "alone",
                "aloof",
                "distant",
                "guarded",
                "isolated",
                "private",
                "reserved",
                "solitary",
                "withdrawn",
            }
        ),
    },
    "stability_change": {
        "stability": frozenset(
            {
                "consistent",
                "predictable",
                "rooted",
                "routine",
                "settled",
                "stable",
                "steady",
            }
        ),
        "change": frozenset(
            {
                "adventurous",
                "change",
                "changing",
                "evolving",
                "novelty",
                "restless",
                "variety",
                "experimental",
            }
        ),
    },
    "approach_avoidance": {
        "approach": frozenset(
            {
                "assertive",
                "bold",
                "confront",
                "direct",
                "initiate",
                "proactive",
                "pursue",
                "risk",
            }
        ),
        "avoidance": frozenset(
            {
                "avoid",
                "avoidance",
                "cautious",
                "hesitate",
                "retreat",
                "risk_averse",
                "riskaverse",
                "withdraw",
            }
        ),
    },
    "structure_spontaneity": {
        "structure": frozenset(
            {
                "deliberate",
                "organized",
                "planful",
                "predictable",
                "routine",
                "structured",
                "systematic",
            }
        ),
        "spontaneity": frozenset(
            {
                "adaptable",
                "flexible",
                "fluid",
                "improvise",
                "improvisational",
                "spontaneous",
                "unstructured",
            }
        ),
    },
}


@dataclass(frozen=True)
class ContradictionFlag:
    """One detected high-confidence contradiction candidate."""

    id: str
    attribute_a_id: str
    attribute_a_domain: str
    attribute_a_label: str
    attribute_a_value: str
    attribute_b_id: str
    attribute_b_domain: str
    attribute_b_label: str
    attribute_b_value: str
    polarity_axis: str
    confidence: float
    status: str
    created_at: datetime


def _parse_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _attribute_text(attribute: dict[str, object]) -> str:
    return " ".join(str(attribute.get(field, "") or "") for field in _TEXT_FIELDS)


def _confidence_value(attribute: dict[str, object]) -> float:
    value = attribute.get("confidence", 0.0)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _polarity_hits(attribute: dict[str, object]) -> dict[str, str]:
    tokens = tokenize(_attribute_text(attribute))
    hits: dict[str, str] = {}
    for axis, poles in _POLARITY_AXES.items():
        for polarity, terms in poles.items():
            if tokens.intersection(terms):
                hits[axis] = polarity
                break
    return hits


def _load_candidate_attributes(conn) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT a.id, d.name, a.label, a.value, a.elaboration, a.confidence, a.status
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE a.status IN ('active', 'confirmed')
          AND a.confidence >= ?
        ORDER BY a.updated_at DESC, a.id DESC
        """,
        (_MIN_CONFIDENCE,),
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "domain": str(row[1]),
            "label": str(row[2]),
            "value": str(row[3]),
            "elaboration": row[4],
            "confidence": float(row[5]),
            "status": str(row[6]),
        }
        for row in rows
    ]


def _flag_key(attribute_a_id: str, attribute_b_id: str, axis: str) -> tuple[str, str, str]:
    ordered = sorted((attribute_a_id, attribute_b_id))
    return ordered[0], ordered[1], axis


def _existing_keys(conn) -> set[tuple[str, str, str]]:
    rows = conn.execute(
        """
        SELECT attribute_a_id, attribute_b_id, polarity_axis
        FROM contradiction_flags
        """
    ).fetchall()
    return {
        _flag_key(str(row[0]), str(row[1]), str(row[2]))
        for row in rows
    }


def _build_confidence(attribute_a: dict[str, object], attribute_b: dict[str, object]) -> float:
    average = (_confidence_value(attribute_a) + _confidence_value(attribute_b)) / 2.0
    if attribute_a.get("status") == "confirmed" and attribute_b.get("status") == "confirmed":
        average += 0.05
    return round(min(max(average, 0.5), 0.95), 2)


def refresh_contradiction_flags(conn) -> list[ContradictionFlag]:
    """Detect and persist new contradiction flags without duplicating prior rows."""
    attributes = _load_candidate_attributes(conn)
    existing = _existing_keys(conn)
    created_at = datetime.now(UTC).isoformat()
    inserted = False

    for index, attribute_a in enumerate(attributes):
        hits_a = _polarity_hits(attribute_a)
        if not hits_a:
            continue
        for attribute_b in attributes[index + 1 :]:
            if attribute_a["id"] == attribute_b["id"]:
                continue
            hits_b = _polarity_hits(attribute_b)
            if not hits_b:
                continue
            shared_axes = set(hits_a).intersection(hits_b)
            for axis in shared_axes:
                if hits_a[axis] == hits_b[axis]:
                    continue
                key = _flag_key(str(attribute_a["id"]), str(attribute_b["id"]), axis)
                if key in existing:
                    continue
                conn.execute(
                    """
                    INSERT INTO contradiction_flags (
                        id,
                        attribute_a_id,
                        attribute_b_id,
                        polarity_axis,
                        confidence,
                        status,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        str(attribute_a["id"]),
                        str(attribute_b["id"]),
                        axis,
                        _build_confidence(attribute_a, attribute_b),
                        created_at,
                    ),
                )
                existing.add(key)
                inserted = True

    if inserted:
        conn.commit()
    return list_pending_contradiction_flags(conn)


def list_pending_contradiction_flags(conn) -> list[ContradictionFlag]:
    """Return pending contradiction flags with attribute context for Teach."""
    rows = conn.execute(
        """
        SELECT
            f.id,
            a.id,
            da.name,
            a.label,
            a.value,
            b.id,
            db.name,
            b.label,
            b.value,
            f.polarity_axis,
            f.confidence,
            f.status,
            f.created_at
        FROM contradiction_flags f
        JOIN attributes a ON a.id = f.attribute_a_id
        JOIN domains da ON da.id = a.domain_id
        JOIN attributes b ON b.id = f.attribute_b_id
        JOIN domains db ON db.id = b.domain_id
        WHERE f.status = 'pending'
          AND a.status IN ('active', 'confirmed')
          AND b.status IN ('active', 'confirmed')
        ORDER BY f.created_at DESC, f.id DESC
        """
    ).fetchall()
    return [
        ContradictionFlag(
            id=str(row[0]),
            attribute_a_id=str(row[1]),
            attribute_a_domain=str(row[2]),
            attribute_a_label=str(row[3]),
            attribute_a_value=str(row[4]),
            attribute_b_id=str(row[5]),
            attribute_b_domain=str(row[6]),
            attribute_b_label=str(row[7]),
            attribute_b_value=str(row[8]),
            polarity_axis=str(row[9]),
            confidence=float(row[10]),
            status=str(row[11]),
            created_at=_parse_timestamp(row[12]),
        )
        for row in rows
    ]
