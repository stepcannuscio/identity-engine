"""Tests for engine/context_assembler.py."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.context_assembler import assemble_query_context


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


@pytest.fixture
def domain_ids(conn):
    rows = conn.execute("SELECT id, name FROM domains").fetchall()
    return {name: domain_id for domain_id, name in rows}


def _insert_attribute(
    conn,
    domain_id: str,
    label: str,
    value: str,
    confidence: float = 0.9,
    routing: str = "local_only",
) -> None:
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status
        )
        VALUES (?, ?, ?, ?, ?, 'stable', 'reflection', ?, ?, 'active')
        """,
        (str(uuid.uuid4()), domain_id, label, value, None, confidence, routing),
    )
    conn.commit()


def test_assemble_query_context_uses_simple_budget_and_flags_trim(conn, domain_ids):
    for i in range(12):
        _insert_attribute(
            conn,
            domain_ids["goals"],
            f"goal_{i}",
            "I want to achieve my goal next quarter.",
            confidence=0.95,
            routing="external_ok",
        )

    context = assemble_query_context(
        "What goals should I focus on next?",
        "simple",
        [],
        conn,
    )

    assert context.retrieval_mode == "simple"
    assert context.attribute_count == 8
    assert context.budget_metadata["max_attributes"] == 8
    assert context.was_trimmed is True
    assert context.contains_local_only is False


def test_assemble_query_context_preserves_domain_intent_fallback(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["goals"],
        "career_direction",
        "Shift toward technical leadership over the next 12 months.",
        confidence=0.9,
    )

    context = assemble_query_context(
        "What are my current goals?",
        "simple",
        [],
        conn,
    )

    assert any(attribute["domain"] == "goals" for attribute in context.attributes)
    assert "goals" in context.domains_used


def test_assemble_query_context_caps_history_and_marks_local_only(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["patterns"],
        "morning_focus",
        "I focus best in the morning.",
        confidence=0.8,
        routing="local_only",
    )
    history = []
    for i in range(7):
        history.append({"role": "user", "content": f"u{i}"})
        history.append({"role": "assistant", "content": f"a{i}"})

    context = assemble_query_context(
        "How do I tend to work?",
        "open_ended",
        history,
        conn,
    )

    assert len(context.session_history) == 12
    assert context.session_history[0]["content"] == "u1"
    assert context.contains_local_only is True
    assert context.was_trimmed is True
