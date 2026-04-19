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
from engine.artifact_ingestion import ingest_artifact
from engine.context_assembler import AssembledContext
from engine.privacy_broker import AuditedRoutingViolationError, BrokeredResult, InferenceDecision
from engine.prompt_builder import RoutingViolationError, build_prompt
from engine.query_classifier import build_query_plan, classify_query, classify_source_profile
from engine.coverage_evaluator import INSUFFICIENT_DATA_MESSAGE
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
    source: str = "reflection",
    last_confirmed: str | None = "2026-04-08T12:00:00+00:00",
) -> None:
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, 'stable', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
        """,
        (
            str(uuid.uuid4()),
            domain_id,
            label,
            value,
            None,
            source,
            confidence,
            routing,
            status,
            last_confirmed,
        ),
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


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Who am I when I am under pressure?", "self_question"),
        ("What do my notes say about how I write?", "evidence_based"),
        ("Rewrite this email so it sounds like me.", "voice_generation"),
        ("Summarize the situation for me.", "general"),
    ],
)
def test_classify_source_profile(prompt, expected):
    assert classify_source_profile(prompt) == expected


def test_build_query_plan_keeps_public_query_type_stable():
    plan = build_query_plan("Rewrite this email so it sounds like me.")

    assert plan.retrieval_mode == "simple"
    assert plan.source_profile == "voice_generation"
    assert "writing_task" in plan.intent_tags
    assert "voice_adaptation" in plan.intent_tags
    assert "voice" in plan.domain_hints
    assert plan.classification_reason


def test_build_query_plan_extracts_planning_domain_hints():
    plan = build_query_plan("How should I plan my week so I stay focused?")

    assert plan.source_profile == "preference_sensitive"
    assert "planning" in plan.intent_tags
    assert "goals" in plan.domain_hints
    assert "patterns" in plan.domain_hints


def test_build_query_plan_prevents_write_term_from_becoming_self_question():
    plan = build_query_plan("Write a quick summary for me.")

    assert plan.source_profile == "general"


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


def test_score_attribute_penalizes_unstable_label_history():
    query_text = "What is my main goal?"
    stable = {
        "label": "primary_goal",
        "value": "Build the product carefully.",
        "domain": "goals",
        "confidence": 0.85,
        "status": "active",
        "source": "reflection",
        "prior_versions": 0,
    }
    unstable = {**stable, "prior_versions": 3}

    assert score_attribute(query_text, stable) > score_attribute(query_text, unstable)


def test_retrieve_attribute_candidates_prefers_recently_confirmed_goal(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["goals"],
        "weekly_priority",
        "Protect focus time for the main project.",
        confidence=0.9,
        routing="external_ok",
        status="confirmed",
        last_confirmed="2026-04-18T12:00:00+00:00",
    )
    _insert_attribute(
        conn,
        domain_ids["goals"],
        "weekly_priority_old",
        "Protect focus time for the main project.",
        confidence=0.9,
        routing="external_ok",
        status="active",
        last_confirmed=None,
    )

    results = retrieve_attributes("What is my main goal this week?", "simple", conn)

    assert results[0]["label"] == "weekly_priority"


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
    assert "Grounded context:" in system
    assert "[identity] priority: Finish project" in system
    assert "[identity] timeline: Next 2 months" in system
    assert "[identity] integrity: Keep promises" in system


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
    assert "Grounded context:" in system
    assert "[preference]" in system
    assert "I prefer concise responses." in system
    assert "Avoid dense long form content." in system


def test_build_prompt_includes_bounded_artifact_evidence_for_local_backend():
    context = AssembledContext(
        task_type="query",
        input_text="What patterns exist in my writing?",
        attributes=[],
        session_history=[],
        domains_used=["voice"],
        attribute_count=0,
        retrieval_mode="open_ended",
        was_trimmed=False,
        contains_local_only=True,
        artifact_chunks=[
            {
                "title": "Writing notebook",
                "chunk_index": 0,
                "content": "I revise heavily, then cut for clarity and rhythm.",
                "routing": "local_only",
            }
        ],
        artifact_count=1,
        artifact_sources=["Writing notebook"],
    )

    messages = build_prompt(context, target_backend="local")

    system = messages[0]["content"]
    assert "Grounded context:" in system
    assert "[artifact] Writing notebook [chunk 1]" in system
    assert "cut for clarity and rhythm" in system


def test_build_prompt_trims_artifact_excerpt_length():
    long_excerpt = "clarity and rhythm " * 50
    context = AssembledContext(
        task_type="query",
        input_text="What patterns exist in my writing?",
        attributes=[],
        session_history=[],
        domains_used=["voice"],
        attribute_count=0,
        retrieval_mode="open_ended",
        source_profile="evidence_based",
        was_trimmed=False,
        contains_local_only=True,
        artifact_chunks=[
            {
                "id": "chunk-1",
                "title": "Writing notebook",
                "chunk_index": 0,
                "content": long_excerpt,
                "routing": "local_only",
            }
        ],
        artifact_count=1,
        artifact_sources=["Writing notebook"],
    )

    system = build_prompt(context, target_backend="local")[0]["content"]

    assert "..." in system
    assert long_excerpt not in system


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
                id, category, subject, signal, strength,
                source, context_json, attribute_id, created_at
            )
            VALUES (
                ?, 'writing_style', 'dense_long_form', 'avoid', 4,
                'explicit_feedback', NULL, NULL, ?
            )
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

    assert prepared.source_profile == "preference_sensitive"
    assert "Grounded context:\n(no grounded context retrieved)" in prepared.messages[0]["content"]
    assert prepared.attributes == []


def test_prepare_query_builds_voice_guidance_for_local_voice_generation(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["voice"],
        "tone",
        "Calm, direct, and lightly warm.",
        confidence=0.95,
        routing="external_ok",
        status="confirmed",
    )
    _insert_attribute(
        conn,
        domain_ids["voice"],
        "preference_writing_style_concise_responses",
        "I prefer concise responses.",
        confidence=0.92,
        routing="local_only",
        status="confirmed",
        source="inferred",
    )
    ingest_artifact(
        conn,
        text="I trim hedging, keep the cadence steady, and avoid sounding theatrical.",
        title="Email sample",
        artifact_type="note",
        source="capture",
        domain="voice",
    )

    session = Session()
    config = SimpleNamespace(is_local=True, provider="ollama", model="llama3.1:8b", api_key=None)

    prepared = prepare_query(
        "Rewrite this email so it sounds like me.",
        session,
        conn,
        config,
    )

    system = prepared.messages[0]["content"]
    assert prepared.source_profile == "voice_generation"
    assert prepared.assembled_context.voice_profile is not None
    assert "Voice guidance:" in system
    assert "Local exemplar snippets:" in system


def test_prepare_query_hides_local_voice_exemplars_for_external_backend(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["voice"],
        "tone",
        "Calm, direct, and lightly warm.",
        confidence=0.95,
        routing="external_ok",
        status="confirmed",
    )
    ingest_artifact(
        conn,
        text="This local sample should never be shown to an external backend.",
        title="Local sample",
        artifact_type="note",
        source="capture",
        domain="voice",
    )

    session = Session()
    config = SimpleNamespace(
        is_local=False,
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key="test-key",  # pragma: allowlist secret
    )

    prepared = prepare_query(
        "Rewrite this email so it sounds like me.",
        session,
        conn,
        config,
    )

    system = prepared.messages[0]["content"]
    assert prepared.source_profile == "voice_generation"
    assert "Voice guidance:" in system
    assert "Local exemplar snippets:" not in system


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
    # Insert confirmed attributes to guarantee low_confidence is not short-circuited.
    # Confirmed attrs (12 + 2 = 14 pts each) in the goals domain are all
    # relevant to the query "What is my main goal?" so retrieval picks them up.
    for label, value in [
        ("priority_goal", "Ship a personal project this quarter."),
        ("secondary_goal", "Read one book per month this year."),
        ("long_term_goal", "Build a sustainable freelance practice."),
    ]:
        _insert_attribute(
            conn,
            domain_ids["goals"],
            label,
            value,
            confidence=0.9,
            status="confirmed",
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
    # Confirmed attributes score 14 pts each; 3 in the goals domain ensures
    # retrieval returns enough to clear the 25-pt insufficient threshold.
    for label, value in [
        ("priority_goal", "Ship a personal project this quarter."),
        ("secondary_goal", "Read one book per month this year."),
        ("long_term_goal", "Build a sustainable freelance practice."),
    ]:
        _insert_attribute(
            conn,
            domain_ids["goals"],
            label,
            value,
            confidence=0.9,
            routing="external_ok",
            status="confirmed",
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
    _insert_attribute(
        conn,
        domain_ids["fears"],
        "fear_of_failure",
        "I get anxious about missing major deadlines.",
        confidence=0.9,
        routing="local_only",
    )
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
        query("What am I afraid of?", session, conn, config)

    assert session.query_count == 0
    assert len(session.routing_log) == 1
    entry = session.routing_log[0]
    assert entry["decision"] == "blocked"


def test_query_blocks_external_backend_when_only_artifact_context_is_available(conn):
    ingest_artifact(
        conn,
        text="My notes on writing keep returning to concise drafts and heavy revision.",
        title="Writing notebook",
        artifact_type="note",
        source="capture",
        domain="voice",
    )
    session = Session()
    config = SimpleNamespace(
        is_local=False,
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key="test-key",  # pragma: allowlist secret
    )

    with pytest.raises(RoutingViolationError):
        query("What patterns exist in my writing?", session, conn, config)

    assert len(session.routing_log) == 1
    assert session.routing_log[0]["decision"] == "blocked"
    assert session.routing_log[0]["contains_local_only_context"] is True
    assert session.routing_log[0]["blocked_external_attributes_count"] == 0
    assert session.routing_log[0]["reason"] == "local_only_context_blocked_for_external_inference"


def test_prepare_query_attaches_coverage_assessment(conn, domain_ids):
    # Confirmed attributes (14 pts each) in the query-relevant domain.
    for label, value in [
        ("priority", "Ship the backend cleanly this quarter."),
        ("secondary_goal", "Read one technical book per month."),
        ("long_term_goal", "Build a sustainable freelance practice."),
    ]:
        _insert_attribute(
            conn,
            domain_ids["goals"],
            label,
            value,
            confidence=0.9,
            routing="external_ok",
            status="confirmed",
        )
    session = Session()
    config = SimpleNamespace(is_local=True, provider="ollama", model="llama3.1:8b", api_key=None)

    prepared = prepare_query("What are my current goals?", session, conn, config)

    assert prepared.coverage.confidence != "insufficient_data"
    assert prepared.coverage.counts.attributes >= 1
    assert prepared.coverage.breakdown.attribute_score > 0
    assert prepared.acquisition.status == "not_needed"


def test_prepare_query_attaches_acquisition_for_missing_domain_context(conn):
    session = Session()
    config = SimpleNamespace(is_local=True, provider="ollama", model="llama3.1:8b", api_key=None)

    prepared = prepare_query("What are my current goals?", session, conn, config)

    assert prepared.coverage.confidence == "insufficient_data"
    assert prepared.acquisition.status == "suggested"
    assert prepared.acquisition.gaps[0].kind == "identity"
    assert prepared.acquisition.gaps[0].domain == "goals"


def test_query_short_circuits_when_coverage_is_insufficient(conn, domain_ids):
    session = Session()
    config = SimpleNamespace(is_local=True, provider="ollama", model="llama3.1:8b", api_key=None)

    with patch("engine.query_engine.PrivacyBroker.generate_grounded_response") as broker_mock:
        result = query("What is my main goal?", session, conn, config)

    assert result == INSUFFICIENT_DATA_MESSAGE
    broker_mock.assert_not_called()
    assert session.query_count == 1
    entry = session.routing_log[0]
    assert entry["decision"] == "skipped_insufficient_data"
