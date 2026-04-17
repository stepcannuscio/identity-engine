"""Shared interview preview/write helpers for CLI and API flows."""

from __future__ import annotations

import datetime
import uuid

from config.settings import LOCAL_ONLY, REFLECTION, STABLE
from engine.interview_catalog import DOMAIN_NAMES, question_belongs_to_domain
from engine.privacy_broker import BrokeredResult, PrivacyBroker


def validate_interview_prompt(domain_name: str, question: str) -> None:
    """Validate that one interview question belongs to the canonical catalog."""
    if domain_name not in DOMAIN_NAMES:
        allowed = ", ".join(sorted(DOMAIN_NAMES))
        raise ValueError(f"Invalid interview domain '{domain_name}'. Expected one of: {allowed}")
    if not question_belongs_to_domain(domain_name, question):
        raise ValueError("Interview question does not belong to the requested domain.")


def preview_interview_answer(
    question: str,
    answer: str,
    domain_name: str,
    provider_config,
) -> list[dict]:
    """Extract interview attributes without writing them to the database."""
    return preview_interview_answer_with_audit(
        question,
        answer,
        domain_name,
        provider_config,
    ).content


def preview_interview_answer_with_audit(
    question: str,
    answer: str,
    domain_name: str,
    provider_config,
) -> BrokeredResult[list[dict]]:
    """Extract interview attributes without writing them to the database."""
    validate_interview_prompt(domain_name, question)
    if not answer.strip():
        raise ValueError("Interview answer is required.")
    result = PrivacyBroker(provider_config).extract_interview_attributes(question, answer)
    return BrokeredResult(content=list(result.content), metadata=result.metadata)


def get_domain_id(conn, domain_name: str) -> str:
    """Return the database id for one domain name."""
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (domain_name,)).fetchone()
    if row is None:
        raise RuntimeError(f"Domain '{domain_name}' not found. Run 'make init' to seed domains.")
    return str(row[0])


def find_existing_active(conn, domain_id: str, label: str):
    """Return the current active/confirmed attribute matching one label, if any."""
    return conn.execute(
        "SELECT id, value, confidence FROM attributes "
        "WHERE domain_id = ? AND label = ? AND status IN ('active', 'confirmed')",
        (domain_id, label),
    ).fetchone()


def _insert_attribute_row(conn, domain_id: str, attr: dict, now: str) -> dict:
    saved = {
        **attr,
        "id": str(uuid.uuid4()),
        "domain_id": domain_id,
        "source": REFLECTION,
        "routing": LOCAL_ONLY,
        "status": "active",
        "confidence": float(attr.get("confidence", 0.8)),
    }
    conn.execute(
        "INSERT INTO attributes "
        "(id, domain_id, label, value, elaboration, mutability, source, "
        "confidence, routing, status, created_at, updated_at, last_confirmed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (
            saved["id"],
            domain_id,
            saved["label"],
            saved["value"],
            saved.get("elaboration"),
            saved.get("mutability", STABLE),
            saved["source"],
            saved["confidence"],
            saved["routing"],
            now,
            now,
            now,
        ),
    )
    return saved


def write_attribute(conn, domain_id: str, attr: dict, old_row) -> str:
    """Write one interview attribute, superseding the current version when needed."""
    now = datetime.datetime.now(datetime.UTC).isoformat()

    if old_row is not None:
        old_id, old_value, old_confidence = old_row
        conn.execute(
            "UPDATE attributes SET status = 'superseded', updated_at = ? WHERE id = ?",
            (now, old_id),
        )
        conn.execute(
            "INSERT INTO attribute_history "
            "(id, attribute_id, previous_value, previous_confidence, reason, changed_at,"
            " changed_by) VALUES (?, ?, ?, ?, ?, ?, 'reflection')",
            (
                str(uuid.uuid4()),
                old_id,
                old_value,
                old_confidence,
                "superseded by interview session",
                now,
            ),
        )
        _insert_attribute_row(conn, domain_id, attr, now)
        conn.commit()
        return "updated"

    _insert_attribute_row(conn, domain_id, attr, now)
    conn.commit()
    return "created"


def save_preview_attributes(conn, attributes: list[dict]) -> list[dict]:
    """Persist accepted interview preview items using interview semantics."""
    saved: list[dict] = []
    for attr in attributes:
        domain_name = str(attr["domain"])
        domain_id = get_domain_id(conn, domain_name)
        existing = find_existing_active(conn, domain_id, str(attr["label"]))
        write_attribute(conn, domain_id, attr, existing)
        row = conn.execute(
            """
            SELECT
                a.id,
                d.name,
                a.label,
                a.value,
                a.elaboration,
                a.mutability,
                a.source,
                a.confidence,
                a.routing,
                a.status,
                a.created_at,
                a.updated_at,
                a.last_confirmed
            FROM attributes a
            JOIN domains d ON d.id = a.domain_id
            WHERE d.name = ? AND a.label = ? AND a.status IN ('active', 'confirmed')
            ORDER BY a.updated_at DESC
            LIMIT 1
            """,
            (domain_name, str(attr["label"])),
        ).fetchone()
        if row is None:
            continue
        saved.append(
            {
                "id": str(row[0]),
                "domain": str(row[1]),
                "label": str(row[2]),
                "value": str(row[3]),
                "elaboration": row[4],
                "mutability": str(row[5]),
                "source": str(row[6]),
                "confidence": float(row[7]),
                "routing": str(row[8]),
                "status": str(row[9]),
                "created_at": str(row[10]),
                "updated_at": str(row[11]),
                "last_confirmed": str(row[12]) if row[12] is not None else None,
            }
        )
    return saved
