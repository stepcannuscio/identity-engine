"""Tests for Teach question planning helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import ProviderConfig
from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.interview_catalog import get_domain_definition
from engine.teach_planner import (
    build_question_generation_messages,
    ensure_question_queue,
    get_next_questions,
    mark_question_answered,
    record_question_feedback,
)


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


@pytest.fixture(autouse=True)
def _disable_dynamic_generation_probes(monkeypatch):
    monkeypatch.setattr("engine.teach_planner._dynamic_generation_available", lambda provider_config: False)


def _config():
    return ProviderConfig(
        provider="ollama",
        api_key=None,
        model="llama3.1:8b",
        is_local=True,
        arch="apple_silicon",
        ram_gb=36.0,
    )


def _insert_attribute(
    conn,
    *,
    domain: str,
    label: str,
    value: str,
    confidence: float = 0.84,
) -> str:
    domain_id = conn.execute("SELECT id FROM domains WHERE name = ?", (domain,)).fetchone()[0]
    attribute_id = f"{domain}-{label}"
    now = "2026-04-20T09:00:00+00:00"
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, 'stable', 'explicit', ?, 'local_only', 'confirmed', ?, ?, ?)
        """,
        (
            attribute_id,
            domain_id,
            label,
            value,
            None,
            confidence,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return attribute_id


def test_build_question_generation_messages_only_uses_sanitized_metadata():
    messages = build_question_generation_messages(
        domain="values",
        attribute_count=1,
        recent_tags=["career", "planning"],
        feedback_count=2,
    )

    serialized = "\n".join(message["content"] for message in messages)
    assert "career" in serialized
    assert "planning" in serialized
    assert "raw answers" not in serialized.lower()


def test_get_next_questions_seeds_catalog_questions(conn):
    questions = get_next_questions(conn, _config(), limit=1)

    assert questions
    assert questions[0].source == "catalog"
    assert questions[0].domain is not None


def test_get_next_questions_skips_dynamic_generation_when_local_provider_is_unavailable(
    conn,
    monkeypatch,
):
    monkeypatch.setattr("engine.teach_planner._dynamic_generation_available", lambda provider_config: False)
    monkeypatch.setattr(
        "engine.teach_planner.PrivacyBroker.generate_grounded_response",
        lambda *args, **kwargs: pytest.fail("dynamic generation should be skipped when no local model is ready"),
    )

    questions = get_next_questions(conn, _config(), limit=1)

    assert questions
    assert questions[0].source == "catalog"


def test_feedback_dismisses_the_current_question(conn):
    question = get_next_questions(conn, _config(), limit=1)[0]

    record_question_feedback(conn, question.id, "duplicate")

    status = conn.execute("SELECT status FROM teach_questions WHERE id = ?", (question.id,)).fetchone()[0]
    assert status == "dismissed"


def test_get_next_questions_does_not_reseed_answered_intent(conn):
    question = get_next_questions(conn, _config(), limit=1)[0]
    original_intent = question.intent_key

    mark_question_answered(conn, question.id)
    next_questions = get_next_questions(conn, _config(), limit=1)

    pending_count = conn.execute(
        "SELECT COUNT(*) FROM teach_questions WHERE intent_key = ? AND status = 'pending'",
        (original_intent,),
    ).fetchone()[0]

    assert pending_count == 0
    assert all(item.intent_key != original_intent for item in next_questions)


def test_get_next_questions_does_not_reseed_dismissed_intent(conn):
    question = get_next_questions(conn, _config(), limit=1)[0]
    original_intent = question.intent_key

    record_question_feedback(conn, question.id, "duplicate")
    next_questions = get_next_questions(conn, _config(), limit=1)

    pending_count = conn.execute(
        "SELECT COUNT(*) FROM teach_questions WHERE intent_key = ? AND status = 'pending'",
        (original_intent,),
    ).fetchone()[0]

    assert pending_count == 0
    assert all(item.intent_key != original_intent for item in next_questions)


def test_get_next_questions_dismisses_legacy_duplicate_pending_intents(conn):
    prompt = "What do you believe about privacy in the modern world?"
    intent_key = "beliefs_what_do_you_believe_about_privacy_in_the_modern_world"
    now = "2026-04-20T09:00:00+00:00"
    conn.executemany(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage,
            asked_count, answer_count, created_at, updated_at
        )
        VALUES (?, ?, 'beliefs', ?, 'catalog', 'pending', 10.0, 'teaching', 0, 0, ?, ?)
        """,
        [
            ("pending-1", prompt, intent_key, now, now),
            ("pending-2", prompt, intent_key, now, now),
        ],
    )
    conn.commit()

    next_questions = get_next_questions(conn, _config(), limit=3)
    rows = conn.execute(
        "SELECT id, status FROM teach_questions WHERE intent_key = ? ORDER BY id",
        (intent_key,),
    ).fetchall()

    pending_ids = [str(row[0]) for row in rows if str(row[1]) == "pending"]
    dismissed_ids = [str(row[0]) for row in rows if str(row[1]) == "dismissed"]

    assert len(pending_ids) == 1
    assert len(dismissed_ids) == 1
    assert sum(1 for item in next_questions if item.intent_key == intent_key) == 1


def test_get_next_questions_dismisses_legacy_duplicate_pending_prompts(conn):
    prompt = "What do you believe about privacy in the modern world?"
    now = "2026-04-20T09:00:00+00:00"
    conn.executemany(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage,
            asked_count, answer_count, created_at, updated_at
        )
        VALUES (?, ?, 'beliefs', ?, 'catalog', 'pending', 10.0, 'teaching', 0, 0, ?, ?)
        """,
        [
            ("pending-prompt-1", prompt, "beliefs_privacy_original", now, now),
            ("pending-prompt-2", prompt, "beliefs_privacy_duplicate", now, now),
        ],
    )
    conn.commit()

    next_questions = get_next_questions(conn, _config(), limit=3)
    rows = conn.execute(
        "SELECT id, status FROM teach_questions WHERE prompt = ? ORDER BY id",
        (prompt,),
    ).fetchall()

    pending_ids = [str(row[0]) for row in rows if str(row[1]) == "pending"]
    dismissed_ids = [str(row[0]) for row in rows if str(row[1]) == "dismissed"]

    assert len(pending_ids) == 1
    assert len(dismissed_ids) == 1
    assert sum(1 for item in next_questions if item.prompt == prompt) == 1


def test_ensure_question_queue_skips_generated_prompt_that_was_already_seen(conn, monkeypatch):
    beliefs = get_domain_definition("beliefs")
    assert beliefs is not None
    beliefs_questions = beliefs["questions"]
    now = "2026-04-20T09:00:00+00:00"
    conn.executemany(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage,
            asked_count, answer_count, created_at, updated_at
        )
        VALUES (?, ?, 'beliefs', ?, 'catalog', 'answered', 10.0, 'teaching', 1, 1, ?, ?)
        """,
        [
            (
                f"answered-{index}",
                prompt,
                f"beliefs_{prompt.lower().replace(' ', '_').replace('?', '')}",
                now,
                now,
            )
            for index, prompt in enumerate(beliefs_questions)
        ],
    )
    conn.commit()

    monkeypatch.setattr("engine.teach_planner._dynamic_generation_available", lambda provider_config: True)
    monkeypatch.setattr(
        "engine.teach_planner.PrivacyBroker.generate_grounded_response",
        lambda self, messages, **kwargs: SimpleNamespace(
            content='{"question":"What do you believe about privacy in the modern world?","intent_key":"beliefs_brand_new"}'
        ),
    )

    ensure_question_queue(conn, _config(), limit=3)

    generated_count = conn.execute(
        "SELECT COUNT(*) FROM teach_questions WHERE intent_key = 'beliefs_brand_new'"
    ).fetchone()[0]
    prompt_count = conn.execute(
        "SELECT COUNT(*) FROM teach_questions WHERE prompt = ?",
        ("What do you believe about privacy in the modern world?",),
    ).fetchone()[0]

    assert generated_count == 0
    assert prompt_count == 1


def test_get_next_questions_prioritizes_synthesis_and_contradiction_reviews(conn):
    _insert_attribute(
        conn,
        domain="personality",
        label="social_orientation",
        value="I am introverted and need quiet after groups.",
    )
    _insert_attribute(
        conn,
        domain="patterns",
        label="meeting_energy",
        value="Big meetings drain my social battery quickly.",
    )
    _insert_attribute(
        conn,
        domain="relationships",
        label="connection_needs",
        value="I connect best in one-on-one conversations.",
    )
    _insert_attribute(
        conn,
        domain="goals",
        label="exploration_style",
        value="I stay creative when I keep things spontaneous and flexible.",
    )
    _insert_attribute(
        conn,
        domain="patterns",
        label="workflow_structure",
        value="I do my best work with highly structured routines and organized plans.",
    )

    questions = get_next_questions(conn, _config(), limit=3)

    assert questions
    assert questions[0].source in {"synthesis", "contradiction"}
    assert any(item.source == "synthesis" for item in questions)
    assert any(item.source == "contradiction" for item in questions)
