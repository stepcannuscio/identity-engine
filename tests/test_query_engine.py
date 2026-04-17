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
from engine.context_assembler import AssembledContext
from engine.privacy_broker import AuditedRoutingViolationError, BrokeredResult, InferenceDecision
from engine.prompt_builder import RoutingViolationError, build_prompt
from engine.query_classifier import classify_query
from engine.query_engine import prepare_query, query
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
    status: str = "active",
) -> None:
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status
        )
        VALUES (?, ?, ?, ?, ?, 'stable', 'reflection', ?, ?, ?)
        """,
        (str(uuid.uuid4()), domain_id, label, value, None, confidence, routing, status),
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


def test_retrieve_attributes_excludes_rejected_attributes(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["goals"],
        "priority",
        "Focus on phase 3 delivery.",
        confidence=0.95,
        status="rejected",
    )

    results = retrieve_attributes("What goals should I focus on next?", "simple", conn)

    assert results == []


def test_score_attribute_prefers_confirmed_status_when_other_signals_match():
    query_text = "What matters most in my values?"
    active = {
        "label": "honesty",
        "value": "Honesty matters most.",
        "domain": "values",
        "confidence": 0.7,
        "status": "active",
    }
    confirmed = {
        **active,
        "status": "confirmed",
    }

    assert score_attribute(query_text, confirmed) > score_attribute(query_text, active)


def test_build_prompt_includes_system_message_first():
    context = AssembledContext(
        task_type="query",
        input_text="What should I do?",
        attributes=[
            {
                "domain": "goals",
                "label": "priority",
                "value": "Finish project",
                "score": 0.8,
                "routing": "external_ok",
            }
        ],
        session_history=[],
        domains_used=["goals"],
        attribute_count=1,
        retrieval_mode="simple",
        was_trimmed=False,
        contains_local_only=False,
    )
    messages = build_prompt(
        context,
        target_backend="local",
    )
    assert messages[0]["role"] == "system"


def test_build_prompt_formats_attributes_grouped_by_domain():
    context = AssembledContext(
        task_type="query",
        input_text="question",
        attributes=[
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
        ],
        session_history=[],
        domains_used=["goals", "values"],
        attribute_count=3,
        retrieval_mode="simple",
        was_trimmed=False,
        contains_local_only=False,
    )
    messages = build_prompt(context, target_backend="local")
    system = messages[0]["content"]
    assert "[goals] priority: Finish project" in system
    assert "[goals] timeline: Next 2 months" in system
    assert "[values] integrity: Keep promises" in system


def test_build_prompt_includes_learned_preference_guidance():
    context = AssembledContext(
        task_type="query",
        input_text="Rewrite this email",
        attributes=[],
        session_history=[],
        domains_used=[],
        attribute_count=0,
        retrieval_mode="simple",
        was_trimmed=False,
        contains_local_only=False,
        preference_summary={
            "task_profiles": [],
            "positive": [
                {
                    "summary": "I prefer concise responses.",
                    "source": "attribute",
                    "status": "confirmed",
                    "routing": "local_only",
                }
            ],
            "negative": [
                {
                    "summary": "Avoid dense long form content.",
                    "source": "signal_summary",
                    "status": "summary",
                    "routing": "local_only",
                }
            ],
        },
    )

    messages = build_prompt(context, target_backend="local")

    system = messages[0]["content"]
    assert "Learned preference guidance:" in system
    assert "Prefer: I prefer concise responses." in system
    assert "Avoid: Avoid dense long form content." in system


def test_build_prompt_caps_history_at_six_exchanges():
    history = []
    for i in range(7):
        history.append({"role": "user", "content": f"u{i}"})
        history.append({"role": "assistant", "content": f"a{i}"})
    context = AssembledContext(
        task_type="query",
        input_text="current",
        attributes=[],
        session_history=history[-12:],
        domains_used=[],
        attribute_count=0,
        retrieval_mode="open_ended",
        was_trimmed=True,
        contains_local_only=False,
    )
    messages = build_prompt(context, target_backend="local")
    # 1 system + 12 capped history + 1 current query
    assert len(messages) == 14
    assert messages[1]["content"] == "u1"
    assert messages[-1]["content"] == "current"


def test_prepare_query_omits_local_signal_summaries_for_external_backend(conn):
    for index in range(3):
        conn.execute(
            """
            INSERT INTO preference_signals (
                id, category, subject, signal, strength, source, context_json, attribute_id, created_at
            )
            VALUES (?, 'writing_style', 'dense_long_form', 'avoid', 4, 'explicit_feedback', NULL, NULL, ?)
            """,
            (str(uuid.uuid4()), f"2026-04-17T12:0{index}:00+00:00"),
        )
    conn.commit()

    session = Session()
    config = SimpleNamespace(
        is_local=False,
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key="test-key",  # pragma: allowlist secret
    )

    prepared = prepare_query(
        "Rewrite this email so it sounds better.",
        session,
        conn,
        config,
    )

    assert "Learned preference guidance:" not in prepared.messages[0]["content"]
    assert prepared.attributes == []


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


def test_query_logs_normalized_audit_entry(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["goals"],
        "priority_goal",
        "I want to ship a personal project this quarter.",
        confidence=0.9,
        routing="external_ok",
    )
    session = Session()
    config = SimpleNamespace(
        is_local=False,
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key="test-key",  # pragma: allowlist secret
    )

    with patch(
        "engine.query_engine.PrivacyBroker.generate_grounded_response",
        return_value=BrokeredResult(
            content="Focused and steady.",
            metadata=InferenceDecision(
                provider="anthropic",
                model="claude-sonnet-4-6",
                is_local=False,
                task_type="query_generation",
                blocked_external_attributes_count=0,
                routing_enforced=True,
                attribute_count=1,
                domains_used=["goals"],
                retrieval_mode="simple",
                contains_local_only_context=False,
            ),
        ),
    ):
        result = query("What is my main goal?", session, conn, config)

    assert result == "Focused and steady."
    assert len(session.routing_log) == 1
    entry = session.routing_log[0]
    assert entry["query"] == "What is my main goal?"
    assert entry["task_type"] == "query_generation"
    assert entry["provider"] == "anthropic"
    assert entry["is_local"] is False
    assert entry["decision"] == "allowed"
    assert entry["domains_referenced"] == ["goals"]


def test_query_logs_blocked_audit_entry_without_incrementing_success_count(conn, domain_ids):
    session = Session()
    config = SimpleNamespace(
        is_local=False,
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key="test-key",  # pragma: allowlist secret
    )

    blocked_audit = InferenceDecision(
        provider="anthropic",
        model="claude-sonnet-4-6",
        is_local=False,
        task_type="query_generation",
        blocked_external_attributes_count=1,
        routing_enforced=True,
        attribute_count=1,
        domains_used=["fears"],
        retrieval_mode="simple",
        contains_local_only_context=True,
        decision="blocked",
        reason="local_only_context_blocked_for_external_inference",
        warning="local_only attributes cannot be sent to external backends",
    )

    with patch(
        "engine.query_engine.PrivacyBroker.generate_grounded_response",
        side_effect=AuditedRoutingViolationError(
            "local_only attributes cannot be sent to external backends: fear_of_failure",
            audit=blocked_audit,
        ),
    ), pytest.raises(RoutingViolationError):
        query("Tell me about my fears", session, conn, config)

    assert session.query_count == 0
    assert len(session.routing_log) == 1
    entry = session.routing_log[0]
    assert entry["decision"] == "blocked"
    assert entry["contains_local_only_context"] is True
    assert entry["blocked_external_attributes_count"] >= 1
    assert entry["reason"] == "local_only_context_blocked_for_external_inference"
