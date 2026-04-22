"""Cross-domain synthesis helpers for Teach review flows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import uuid

from engine.concept_expander import describe_concept_key, matching_concept_keys_for_text
from engine.contradiction_detector import (
    ContradictionFlag,
    list_pending_contradiction_flags,
    refresh_contradiction_flags,
)

_MIN_SYNTHESIS_CONFIDENCE = 0.55
_MIN_SYNTHESIS_DOMAINS = 3
_MAX_EVIDENCE_REFERENCES = 4


@dataclass(frozen=True)
class CrossDomainSynthesis:
    """One reviewable multi-domain theme staged for Teach."""

    id: str
    theme_label: str
    domains_involved: list[str]
    strength: float
    synthesis_text: str | None
    evidence_ids: list[str]
    status: str
    created_at: datetime


@dataclass(frozen=True)
class CrossDomainRefreshResult:
    """Pending cross-domain insights after a refresh pass."""

    syntheses: list[CrossDomainSynthesis]
    contradictions: list[ContradictionFlag]


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
    return [str(item) for item in parsed if str(item).strip()]


def _canonical_json(items: list[str]) -> str:
    return json.dumps(sorted({str(item) for item in items if str(item).strip()}), separators=(",", ":"))


def _load_active_attributes(conn) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT a.id, d.name, a.label, a.value, a.elaboration, a.confidence
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE a.status IN ('active', 'confirmed')
          AND a.confidence >= ?
        ORDER BY a.updated_at DESC, a.id DESC
        """,
        (_MIN_SYNTHESIS_CONFIDENCE,),
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "domain": str(row[1]),
            "label": str(row[2]),
            "value": str(row[3]),
            "elaboration": row[4],
            "confidence": float(row[5]),
        }
        for row in rows
    ]


def _attribute_text(attribute: dict[str, object]) -> str:
    return " ".join(
        str(attribute.get(field, "") or "")
        for field in ("label", "value", "elaboration")
    )


def _confidence_value(attribute: dict[str, object]) -> float:
    value = attribute.get("confidence", 0.0)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _build_strength(attributes: list[dict[str, object]], domains: list[str]) -> float:
    average_confidence = sum(_confidence_value(item) for item in attributes) / max(len(attributes), 1)
    domain_factor = min(len(domains), 5) / 5.0
    evidence_factor = min(len(attributes), 6) / 6.0
    strength = (average_confidence * 0.50) + (domain_factor * 0.30) + (evidence_factor * 0.20)
    return round(min(max(strength, 0.4), 0.95), 2)


def _build_synthesis_text(theme_label: str, attributes: list[dict[str, object]], domains: list[str]) -> str:
    evidence_refs = [
        f"{item['domain']}:{item['label']}"
        for item in sorted(
            attributes,
            key=lambda item: (_confidence_value(item), str(item["domain"]), str(item["label"])),
            reverse=True,
        )[:_MAX_EVIDENCE_REFERENCES]
    ]
    if evidence_refs:
        evidence_summary = ", ".join(evidence_refs)
        return (
            f"A {theme_label} thread appears across {', '.join(domains)}. "
            f"Supporting signals include {evidence_summary}."
        )
    return f"A {theme_label} thread appears across {', '.join(domains)}."


def _existing_synthesis_rows(conn) -> dict[tuple[str, str], tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT id, theme_label, evidence_ids_json, status
        FROM cross_domain_synthesis
        """
    ).fetchall()
    return {
        (str(row[1]), str(row[2])): (str(row[0]), str(row[3]))
        for row in rows
    }


def refresh_cross_domain_synthesis(conn) -> list[CrossDomainSynthesis]:
    """Detect and stage multi-domain themes without duplicating prior review items."""
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for attribute in _load_active_attributes(conn):
        matches = matching_concept_keys_for_text(
            _attribute_text(attribute),
            domain=str(attribute["domain"]),
        )
        for key in matches:
            grouped[key].append(attribute)

    existing = _existing_synthesis_rows(conn)
    inserted_or_updated = False
    computed_at = datetime.now(UTC).isoformat()

    for key, attributes in grouped.items():
        domains = sorted({str(attribute["domain"]) for attribute in attributes})
        if len(domains) < _MIN_SYNTHESIS_DOMAINS:
            continue

        theme_label = describe_concept_key(key)
        evidence_ids = sorted({str(attribute["id"]) for attribute in attributes})
        evidence_json = _canonical_json(evidence_ids)
        domains_json = _canonical_json(domains)
        strength = _build_strength(attributes, domains)
        synthesis_text = _build_synthesis_text(theme_label, attributes, domains)

        existing_row = existing.get((theme_label, evidence_json))
        if existing_row is None:
            conn.execute(
                """
                INSERT INTO cross_domain_synthesis (
                    id,
                    theme_label,
                    domains_involved_json,
                    strength,
                    synthesis_text,
                    evidence_ids_json,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending_review', ?)
                """,
                (
                    str(uuid.uuid4()),
                    theme_label,
                    domains_json,
                    strength,
                    synthesis_text,
                    evidence_json,
                    computed_at,
                ),
            )
            inserted_or_updated = True
            continue

        existing_id, existing_status = existing_row
        if existing_status != "pending_review":
            continue
        conn.execute(
            """
            UPDATE cross_domain_synthesis
            SET domains_involved_json = ?,
                strength = ?,
                synthesis_text = ?
            WHERE id = ?
            """,
            (domains_json, strength, synthesis_text, existing_id),
        )
        inserted_or_updated = True

    if inserted_or_updated:
        conn.commit()
    return list_pending_cross_domain_syntheses(conn)


def list_pending_cross_domain_syntheses(conn) -> list[CrossDomainSynthesis]:
    """Return pending staged syntheses for Teach review."""
    rows = conn.execute(
        """
        SELECT
            id,
            theme_label,
            domains_involved_json,
            strength,
            synthesis_text,
            evidence_ids_json,
            status,
            created_at
        FROM cross_domain_synthesis
        WHERE status = 'pending_review'
        ORDER BY strength DESC, created_at DESC, id DESC
        """
    ).fetchall()
    return [
        CrossDomainSynthesis(
            id=str(row[0]),
            theme_label=str(row[1]),
            domains_involved=_json_list(row[2]),
            strength=float(row[3]),
            synthesis_text=str(row[4]) if row[4] not in {None, ""} else None,
            evidence_ids=_json_list(row[5]),
            status=str(row[6]),
            created_at=_parse_timestamp(row[7]),
        )
        for row in rows
    ]


def refresh_cross_domain_intelligence(conn) -> CrossDomainRefreshResult:
    """Refresh both synthesis and contradiction staging state."""
    syntheses = refresh_cross_domain_synthesis(conn)
    contradictions = refresh_contradiction_flags(conn)
    return CrossDomainRefreshResult(
        syntheses=syntheses,
        contradictions=contradictions,
    )


def list_pending_cross_domain_intelligence(conn) -> CrossDomainRefreshResult:
    """Return staged cross-domain syntheses and contradiction flags."""
    return CrossDomainRefreshResult(
        syntheses=list_pending_cross_domain_syntheses(conn),
        contradictions=list_pending_contradiction_flags(conn),
    )
