"""Tests for the Phase 2 identity query engine modules."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.privacy_broker import BrokeredResult, InferenceDecision
from engine.prompt_builder import build_prompt
from engine.query_classifier import classify_query
from engine.query_engine import query
from engine.retriever import OPEN_ENDED_BUDGET, SIMPLE_BUDGET, retrieve_attributes, score_attribute
from engine.session import Session


@pytest.fixture
def conn():
    """In-memory DB with schema + seeded domains."""
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


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Who am I?", "simple"),
        ("what is my voice", "simple"),
        ("What are my values right now?", "simple"),
        ("Do I avoid conflict?", "simple"),
        ("Am I consistent under stress?", "simple"),
        ("list my goals for this year", "simple"),
        ("show me my relationships patterns", "simple"),
        ("Tell me how I should plan because my role changed rapidly?", "open_ended"),
        ("How should I adapt when priorities shift at work and at home over months?", "open_ended"),
        ("What patterns emerge, if I reflect on deadlines and trust in teams?", "simple"),
    ],
)
def test_classify_query_edge_cases(prompt, expected):
    assert classify_query(prompt) == expected


def test_classify_query_returns_simple_for_short_direct_query():
    assert classify_query("My goals?") == "simple"


def test_classify_query_returns_open_ended_for_complex_query():
    prompt = "Describe how competing priorities evolve across months under uncertainty."
    assert classify_query(prompt) == "open_ended"


def test_score_attribute_higher_for_direct_keyword_match():
    query_text = "What goal am I trying to achieve next?"
    direct = {
        "label": "primary_goal",
        "value": "Achieve promotion this year.",
        "domain": "goals",
        "confidence": 0.7,
    }
    indirect = {
        "label": "friendship_style",
        "value": "I keep a small trusted circle.",
        "domain": "relationships",
        "confidence": 0.7,
    }
    assert score_attribute(query_text, direct) > score_attribute(query_text, indirect)


def test_score_attribute_applies_domain_bonus_correctly():
    query_text = "I want to plan my future goals."
    in_domain = {
        "label": "plan_horizon",
        "value": "I plan quarterly.",
        "domain": "goals",
        "confidence": 0.5,
    }
    out_domain = {
        "label": "tone",
        "value": "I write plainly.",
        "domain": "voice",
        "confidence": 0.5,
    }
    assert score_attribute(query_text, in_domain) > score_attribute(query_text, out_domain)


def test_retrieve_attributes_respects_simple_budget_max_attributes(conn, domain_ids):
    for i in range(15):
        _insert_attribute(
            conn,
            domain_ids["goals"],
            f"goal_{i}",
            "I want to achieve my goal next quarter.",
            confidence=0.95,
        )
    results = retrieve_attributes("What goals should I focus on next?", "simple", conn)
    assert len(results) == SIMPLE_BUDGET["max_attributes"]


def test_retrieve_attributes_respects_open_ended_budget_max_attributes(conn, domain_ids):
    for i in range(30):
        domain = "goals" if i % 2 == 0 else "patterns"
        _insert_attribute(
            conn,
            domain_ids[domain],
            f"item_{i}",
            "I usually plan next actions and review habits.",
            confidence=0.95,
            routing="external_ok",
        )
    results = retrieve_attributes(
        "How do my goals and habits interact over the next year in practical terms?",
        "open_ended",
        conn,
    )
    assert len(results) == OPEN_ENDED_BUDGET["max_attributes"]


def test_retrieve_attributes_filters_below_score_threshold(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["beliefs"],
        "distant_topic",
        "Completely unrelated text fragment.",
        confidence=0.05,
    )
    results = retrieve_attributes("What goals should I pursue?", "simple", conn)
    assert results == []


def test_retrieve_attributes_domain_intent_fallback_includes_goals(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["goals"],
        "career_direction",
        "Shift toward technical leadership over the next 12 months.",
        confidence=0.9,
    )
    results = retrieve_attributes("What are my current goals?", "simple", conn)
    assert len(results) >= 1
    assert any(attr["domain"] == "goals" for attr in results)


def test_build_prompt_includes_system_message_first():
    attributes = [
        {
            "domain": "goals",
            "label": "priority",
            "value": "Finish project",
            "score": 0.8,
            "routing": "external_ok",
        }
    ]
    messages = build_prompt(
        "What should I do?", attributes, [], "simple", target_backend="local"
    )
    assert messages[0]["role"] == "system"


def test_build_prompt_formats_attributes_grouped_by_domain():
    attributes = [
        {
            "domain": "goals",
            "label": "priority",
            "value": "Finish project",
            "score": 0.9,
            "routing": "external_ok",
        },
        {
            "domain": "goals",
            "label": "timeline",
            "value": "Next 2 months",
            "score": 0.7,
            "routing": "external_ok",
        },
        {
            "domain": "values",
            "label": "integrity",
            "value": "Keep promises",
            "score": 0.6,
            "routing": "external_ok",
        },
    ]
    messages = build_prompt("question", attributes, [], "simple", target_backend="local")
    system = messages[0]["content"]
    assert "[goals] priority: Finish project" in system
    assert "[goals] timeline: Next 2 months" in system
    assert "[values] integrity: Keep promises" in system


def test_build_prompt_caps_history_at_six_exchanges():
    history = []
    for i in range(7):
        history.append({"role": "user", "content": f"u{i}"})
        history.append({"role": "assistant", "content": f"a{i}"})
    messages = build_prompt("current", [], history, "open_ended", target_backend="local")
    # 1 system + 12 capped history + 1 current query
    assert len(messages) == 14
    assert messages[1]["content"] == "u1"
    assert messages[-1]["content"] == "current"


def test_session_add_exchange_drops_oldest_when_cap_exceeded():
    session = Session()
    for i in range(7):
        session.add_exchange(f"q{i}", f"a{i}")
    assert len(session.history) == 12
    assert session.history[0]["content"] == "q1"


def test_session_add_exchange_never_drops_most_recent_exchange():
    session = Session()
    for i in range(7):
        session.add_exchange(f"q{i}", f"a{i}")
    assert session.history[-2]["content"] == "q6"
    assert session.history[-1]["content"] == "a6"


def test_session_to_db_record_returns_correct_external_calls_count():
    session = Session()
    session.log_query("q1", "simple", "local", 2, ["goals"])
    session.log_query("q2", "open_ended", "anthropic", 4, ["goals", "values"])
    session.log_query("q3", "simple", "groq", 1, [])
    session.query_count = 3
    record = session.to_db_record()
    assert record["external_calls_made"] == 2
    assert record["session_type"] == "freeform"


def test_query_returns_string(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["goals"],
        "priority_goal",
        "I want to ship a personal project this quarter.",
        confidence=0.9,
    )
    session = Session()
    config = SimpleNamespace(is_local=True, provider="ollama", model="llama3.1:8b", api_key=None)

    with patch(
        "engine.query_engine.PrivacyBroker.generate_grounded_response",
        return_value=BrokeredResult(
            content="Focused and steady.",
            metadata=InferenceDecision(
                provider="ollama",
                model="llama3.1:8b",
                is_local=True,
                task_type="query_generation",
                blocked_external_attributes_count=0,
                routing_enforced=True,
            ),
        ),
    ):
        result = query("What is my main goal?", session, conn, config)

    assert isinstance(result, str)
    assert result == "Focused and steady."
