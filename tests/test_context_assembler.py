"""Tests for engine/context_assembler.py."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.preference_signals import PreferenceSignalInput, record_preference_signal
from db.schema import create_tables, seed_domains
from engine.artifact_ingestion import ingest_artifact
from engine.context_assembler import assemble_query_context
from engine.query_classifier import build_query_plan


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


def _record_signal(
    conn,
    *,
    category: str,
    subject: str,
    signal: str,
    strength: int = 3,
) -> None:
    record_preference_signal(
        conn,
        PreferenceSignalInput(
            category=category,
            subject=subject,
            signal=signal,
            strength=strength,
        ),
    )


def _assemble(query: str, conn, history: list[dict] | None = None):
    plan = build_query_plan(query)
    return assemble_query_context(
        query,
        plan.retrieval_mode,
        plan.source_profile,
        history or [],
        conn,
        intent_tags=plan.intent_tags,
        domain_hints=plan.domain_hints,
    )


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

    context = _assemble("What goals should I focus on next?", conn)

    assert context.retrieval_mode == "simple"
    assert context.source_profile == "self_question"
    assert context.attribute_count == 6
    assert context.budget_metadata["max_attributes"] == 8
    assert context.budget_metadata["max_evidence_items"] == 8
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

    context = _assemble("What are my current goals?", conn)

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

    context = _assemble("How do I tend to work?", conn, history)

    assert len(context.session_history) == 12
    assert context.session_history[0]["content"] == "u1"
    assert context.contains_local_only is True
    assert context.was_trimmed is True


def test_assemble_query_context_includes_relevant_writing_preferences(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["voice"],
        "preference_writing_style_concise_responses",
        "I prefer concise responses.",
        confidence=0.9,
        routing="local_only",
    )

    context = _assemble("Help me draft a short email update.", conn)

    assert context.preference_count == 1
    assert context.preference_attributes[0]["label"] == "preference_writing_style_concise_responses"
    assert any(
        "concise responses" in item["summary"].lower()
        for item in context.preference_summary["positive"]
    )


def test_assemble_query_context_excludes_irrelevant_preference_context(conn, domain_ids):
    for _ in range(3):
        _record_signal(
            conn,
            category="books",
            subject="history",
            signal="like",
            strength=4,
        )

    context = _assemble("What are my values?", conn)

    assert context.preference_count == 0
    assert context.preference_summary["positive"] == []
    assert context.preference_summary["negative"] == []


def test_assemble_query_context_bounds_preference_context(conn, domain_ids):
    for index in range(5):
        _insert_attribute(
            conn,
            domain_ids["voice"],
            f"preference_writing_style_item_{index}",
            f"I prefer concise writing pattern {index}.",
            confidence=0.8,
            routing="local_only",
        )
    for index in range(4):
        _record_signal(
            conn,
            category="writing_style",
            subject=f"detail_level_{index}",
            signal="prefer",
            strength=4,
        )

    context = _assemble("Rewrite this draft and improve the tone.", conn)

    assert context.source_profile == "preference_sensitive"
    assert len(context.preference_attributes) <= 4
    assert context.preference_count <= 7
    assert context.budget_metadata["max_preference_attributes"] == 2
    assert context.budget_metadata["max_preference_signal_summaries"] == 2


def test_assemble_query_context_includes_artifact_chunks_for_open_ended_queries(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["goals"],
        "priority",
        "Ship the backend cleanly.",
        confidence=0.9,
        routing="external_ok",
    )
    ingest_artifact(
        conn,
        text="My notes keep returning to writing tone, revision rhythm, and concise drafts.",
        title="Writing notebook",
        artifact_type="note",
        source="capture",
        domain="voice",
    )

    context = _assemble("What patterns exist in my writing?", conn)

    assert context.artifact_count >= 1
    assert context.artifact_chunks[0]["title"] == "Writing notebook"
    assert "Writing notebook" in context.artifact_sources
    assert context.contains_local_only is True


def test_assemble_query_context_skips_artifact_chunks_for_strong_simple_context(conn, domain_ids):
    for index in range(4):
        _insert_attribute(
            conn,
            domain_ids["goals"],
            f"priority_{index}",
            "I want to finish the current phase this quarter.",
            confidence=0.95,
            routing="external_ok",
        )
    ingest_artifact(
        conn,
        text="Long freeform journal about unrelated writing rituals.",
        title="Journal",
        artifact_type="journal",
        source="upload",
        domain="voice",
    )

    context = _assemble("What goals should I focus on next?", conn)

    assert context.artifact_count == 0
    assert context.artifact_chunks == []


def test_self_questions_rank_confirmed_identity_above_artifacts(conn, domain_ids):
    attribute_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status
        )
        VALUES (?, ?, 'primary_goal', 'Ship the product carefully.', NULL, 'stable',
                'reflection', 0.95, 'external_ok', 'confirmed')
        """,
        (attribute_id, domain_ids["goals"]),
    )
    conn.commit()
    ingest_artifact(
        conn,
        text="My notes say I keep returning to the goal of shipping the product carefully.",
        title="Goal notes",
        artifact_type="note",
        source="capture",
        domain="goals",
    )

    context = _assemble("What are my goals?", conn)

    assert context.source_profile == "self_question"
    assert context.evidence_items[0].source_type == "identity"
    artifact_items = [item for item in context.evidence_items if item.source_type == "artifact"]
    assert artifact_items
    assert context.evidence_items[0].final_score > artifact_items[0].final_score


def test_evidence_based_queries_pull_artifacts_even_with_identity_present(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["voice"],
        "writing_style",
        "I aim for concise, clear writing.",
        confidence=0.9,
        routing="external_ok",
    )
    ingest_artifact(
        conn,
        text="My writing notes show I revise heavily, then cut for rhythm and clarity.",
        title="Writing notebook",
        artifact_type="note",
        source="capture",
        domain="voice",
    )

    context = _assemble("What do my notes say about how I write?", conn)

    assert context.source_profile == "evidence_based"
    assert context.artifact_count >= 1
    assert context.evidence_items[0].source_type == "artifact"


def test_preference_sensitive_queries_rank_preferences_ahead_of_artifacts(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["voice"],
        "preference_writing_style_concise_responses",
        "I prefer concise responses.",
        confidence=0.9,
        routing="local_only",
    )
    ingest_artifact(
        conn,
        text="A past draft rambled and ran long before I cut it down.",
        title="Draft notebook",
        artifact_type="note",
        source="capture",
        domain="voice",
    )

    context = _assemble("Rewrite this email.", conn)

    assert context.source_profile == "preference_sensitive"
    assert context.evidence_items[0].source_type == "preference"
    artifact_items = [item for item in context.evidence_items if item.source_type == "artifact"]
    if artifact_items:
        assert context.evidence_items[0].final_score > artifact_items[0].final_score


def test_voice_generation_queries_compile_voice_profile(conn, domain_ids):
    _insert_attribute(
        conn,
        domain_ids["voice"],
        "tone",
        "Calm, direct, and lightly warm.",
        confidence=0.95,
        routing="external_ok",
    )
    _insert_attribute(
        conn,
        domain_ids["voice"],
        "preference_writing_style_concise_responses",
        "I prefer concise responses.",
        confidence=0.92,
        routing="local_only",
    )
    ingest_artifact(
        conn,
        text="A writing sample where I cut hedging and keep the rhythm tight.",
        title="Email sample",
        artifact_type="note",
        source="capture",
        domain="voice",
    )

    context = _assemble("Rewrite this email so it sounds like me.", conn)

    assert context.source_profile == "voice_generation"
    assert context.voice_profile is not None
    assert context.voice_profile.identity_lines
    assert context.voice_profile.preference_lines
    assert context.voice_profile.exemplar_lines


def test_evidence_based_ranking_penalizes_duplicate_artifact_chunks(conn):
    ingest_artifact(
        conn,
        text=(
            "My writing notes focus on clarity and rhythm. "
            "I keep revising until the sentences feel clean. "
            "I cut dense sections aggressively."
        ),
        title="Notebook A",
        artifact_type="note",
        source="capture",
        domain="voice",
    )
    ingest_artifact(
        conn,
        text="Another set of writing notes says I shorten drafts and keep the tone crisp.",
        title="Notebook B",
        artifact_type="note",
        source="capture",
        domain="voice",
    )

    context = _assemble("What do my notes say about my writing?", conn)

    artifact_items = [item for item in context.evidence_items if item.source_type == "artifact"]
    assert len(artifact_items) >= 2
    assert artifact_items[0].title_or_label != artifact_items[1].title_or_label
