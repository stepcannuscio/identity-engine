"""Persistence helpers for local-only textual voice fidelity feedback."""

from __future__ import annotations

from dataclasses import dataclass
import json
import uuid


@dataclass(frozen=True)
class VoiceFeedbackInput:
    """Validated payload for one voice-fidelity feedback submission."""

    query_feedback_id: str | None
    session_id: str | None
    query_text: str
    response_text: str
    feedback: str
    notes: str | None
    backend: str
    query_type: str
    source_profile: str
    intent_tags: list[str]
    domains_referenced: list[str]


def record_voice_feedback(conn, payload: VoiceFeedbackInput) -> str:
    """Persist one local-only voice feedback row and return its id."""
    feedback_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO voice_feedback (
            id,
            query_feedback_id,
            session_id,
            query_text,
            response_text,
            feedback,
            notes,
            backend,
            query_type,
            source_profile,
            intent_tags_json,
            domains_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feedback_id,
            payload.query_feedback_id,
            payload.session_id,
            payload.query_text,
            payload.response_text,
            payload.feedback,
            payload.notes,
            payload.backend,
            payload.query_type,
            payload.source_profile,
            json.dumps(sorted(set(payload.intent_tags))),
            json.dumps(sorted(set(payload.domains_referenced))),
        ),
    )
    conn.commit()
    return feedback_id
