"""Tests for deterministic query-feedback calibration helpers."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.query_feedback import QueryFeedbackInput, record_query_feedback
from db.schema import create_tables, seed_domains
from engine.coverage_evaluator import evaluate_coverage
from engine.context_assembler import AssembledContext
from engine.feedback_calibrator import (
    build_recent_feedback_gap_note,
    load_retrieval_calibration,
    maybe_run_feedback_calibration,
    recompute_retrieval_calibration,
)
from engine.retriever import retrieve_attribute_candidates


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as connection:
        create_tables(connection)
        seed_domains(connection)
        yield connection


def _record_feedback(
    conn,
    *,
    feedback: str,
    source_profile: str = "preference_sensitive",
    domains_referenced: list[str] | None = None,
    domain_hints: list[str] | None = None,
    retrieved_attribute_ids: list[str] | None = None,
) -> None:
    record_query_feedback(
        conn,
        QueryFeedbackInput(
            session_id=None,
            query_text="How should I plan this?",
            response_text="Try a simple next step.",
            feedback=feedback,
            notes=None,
            backend="local",
            query_type="simple",
            source_profile=source_profile,
            confidence="low_confidence",
            intent_tags=["planning"],
            domain_hints=domain_hints or ["goals"],
            domains_referenced=domains_referenced or ["goals"],
            retrieved_attribute_ids=retrieved_attribute_ids or [],
        ),
    )


def _insert_attribute(
    conn,
    *,
    domain: str,
    label: str,
    value: str,
) -> str:
    domain_id = conn.execute(
        "SELECT id FROM domains WHERE name = ?",
        (domain,),
    ).fetchone()[0]
    attribute_id = str(uuid.uuid4())
    now = "2026-04-20T12:00:00+00:00"
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, 'stable', 'explicit', 0.8, 'external_ok', 'active', ?, ?, ?)
        """,
        (
            attribute_id,
            domain_id,
            label,
            value,
            None,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return attribute_id


def _context_with_low_confidence() -> AssembledContext:
    attributes = [
        {
            "domain": "goals",
            "label": f"goal_{idx}",
            "value": "Ship the next phase",
            "status": "active",
            "confidence": 0.75,
            "routing": "external_ok",
            "source": "explicit",
            "updated_at": "2026-04-20T12:00:00+00:00",
            "last_confirmed": None,
        }
        for idx in range(3)
    ]
    return AssembledContext(
        task_type="query",
        input_text="test",
        attributes=attributes,
        session_history=[],
        domains_used=["goals"],
        attribute_count=len(attributes),
        retrieval_mode="simple",
        source_profile="preference_sensitive",
        intent_tags=["planning"],
    )


def test_maybe_run_feedback_calibration_waits_for_trigger_batch(conn):
    for _ in range(9):
        _record_feedback(conn, feedback="missed_context")

    assert maybe_run_feedback_calibration(conn) is False
    count = conn.execute("SELECT COUNT(*) FROM retrieval_calibration").fetchone()[0]
    assert count == 0

    _record_feedback(conn, feedback="missed_context")

    assert maybe_run_feedback_calibration(conn) is True
    count = conn.execute("SELECT COUNT(*) FROM retrieval_calibration").fetchone()[0]
    assert count > 0


def test_recompute_retrieval_calibration_creates_bounded_domain_deltas(conn):
    for _ in range(4):
        _record_feedback(conn, feedback="missed_context", domains_referenced=["goals"])
    for _ in range(4):
        _record_feedback(conn, feedback="helpful", domains_referenced=["voice"], domain_hints=["voice"])

    inserted = recompute_retrieval_calibration(conn)

    assert inserted == 8
    calibration = load_retrieval_calibration(conn, source_profile="preference_sensitive")
    assert calibration["goals"] == pytest.approx(-0.15)
    assert calibration["voice"] == pytest.approx(0.15)


def test_retrieve_attribute_candidates_applies_source_profile_calibration(conn):
    for _ in range(4):
        _record_feedback(conn, feedback="helpful", domains_referenced=["goals"])
    for _ in range(4):
        _record_feedback(conn, feedback="missed_context", domains_referenced=["voice"], domain_hints=["voice"])
    recompute_retrieval_calibration(conn)

    _insert_attribute(conn, domain="voice", label="voice_style", value="I write with warmth.")
    _insert_attribute(conn, domain="goals", label="plan_horizon", value="I plan in quarterly arcs.")

    results = retrieve_attribute_candidates(
        "What direction fits me?",
        "simple",
        conn,
        source_profile="preference_sensitive",
    )

    assert results
    assert results[0]["domain"] == "goals"


def test_low_confidence_coverage_note_can_append_recent_feedback_gap(conn):
    for _ in range(3):
        _record_feedback(conn, feedback="missed_context", domains_referenced=["goals"])

    note = build_recent_feedback_gap_note(
        conn,
        domains=["goals"],
        source_profile="preference_sensitive",
    )
    assessment = evaluate_coverage(
        _context_with_low_confidence(),
        backend="local",
        feedback_gap_note=note,
    )

    assert note is not None
    assert assessment.confidence == "low_confidence"
    assert assessment.notes is not None
    assert "missed context" in assessment.notes


def test_recompute_retrieval_calibration_can_lower_inferred_attribute_confidence(conn):
    attribute_id = _insert_attribute(
        conn,
        domain="goals",
        label="plan_horizon",
        value="I plan in quarterly arcs.",
    )
    conn.execute(
        "UPDATE attributes SET source = 'inferred' WHERE id = ?",
        (attribute_id,),
    )
    conn.commit()

    for _ in range(5):
        _record_feedback(
            conn,
            feedback="missed_context",
            domains_referenced=["goals"],
            retrieved_attribute_ids=[attribute_id],
        )

    recompute_retrieval_calibration(conn)

    row = conn.execute(
        "SELECT confidence FROM attributes WHERE id = ?",
        (attribute_id,),
    ).fetchone()
    assert row is not None
    assert float(row[0]) == pytest.approx(0.70)

    history_row = conn.execute(
        """
        SELECT previous_confidence, changed_by, reason
        FROM attribute_history
        WHERE attribute_id = ?
        ORDER BY changed_at DESC, id DESC
        LIMIT 1
        """,
        (attribute_id,),
    ).fetchone()
    assert history_row is not None
    assert float(history_row[0]) == pytest.approx(0.8)
    assert history_row[1] == "inferred"
    assert str(history_row[2]).startswith("feedback_calibration:")
