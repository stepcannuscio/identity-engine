"""Tests for Teach question planning helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import ProviderConfig
from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.teach_planner import (
    build_question_generation_messages,
    get_next_questions,
    record_question_feedback,
)


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


def _config():
    return ProviderConfig(
        provider="ollama",
        api_key=None,
        model="llama3.1:8b",
        is_local=True,
        arch="apple_silicon",
        ram_gb=36.0,
    )


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


def test_feedback_dismisses_the_current_question(conn):
    question = get_next_questions(conn, _config(), limit=1)[0]

    record_question_feedback(conn, question.id, "duplicate")

    status = conn.execute("SELECT status FROM teach_questions WHERE id = ?", (question.id,)).fetchone()[0]
    assert status == "dismissed"
