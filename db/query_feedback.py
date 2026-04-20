"""Persistence helpers for local-only query usefulness feedback."""

from __future__ import annotations

from dataclasses import dataclass
import json
import uuid

from db.evidence import register_query_feedback_evidence


@dataclass(frozen=True)
class QueryFeedbackInput:
    """Validated payload for one query-feedback submission."""

    session_id: str | None
    query_text: str
    response_text: str
    feedback: str
    notes: str | None
    backend: str
    query_type: str
    source_profile: str
    confidence: str
    intent_tags: list[str]
    domain_hints: list[str]
    domains_referenced: list[str]


def record_query_feedback(conn, payload: QueryFeedbackInput) -> str:
    """Persist one local-only query feedback row and return its id."""
    feedback_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO query_feedback (
            id,
            session_id,
            query_text,
            response_text,
            feedback,
            notes,
            backend,
            query_type,
            source_profile,
            confidence,
            intent_tags_json,
            domain_hints_json,
            domains_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feedback_id,
            payload.session_id,
            payload.query_text,
            payload.response_text,
            payload.feedback,
            payload.notes,
            payload.backend,
            payload.query_type,
            payload.source_profile,
            payload.confidence,
            json.dumps(sorted(set(payload.intent_tags))),
            json.dumps(sorted(set(payload.domain_hints))),
            json.dumps(sorted(set(payload.domains_referenced))),
        ),
    )
    register_query_feedback_evidence(conn, feedback_id=feedback_id)
    conn.commit()
    return feedback_id
