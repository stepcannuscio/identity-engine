"""Tests for deterministic preference promotion."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from db.preference_signals import PreferenceSignalInput, record_preference_signal
from engine.preference_promotion import (
    aggregate_signals,
    evaluate_promotion,
    run_preference_promotion,
)


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


def _domain_id(conn, name: str) -> str:
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (name,)).fetchone()
    assert row is not None
    return str(row[0])


def _insert_attribute(
    conn,
    *,
    domain: str,
    label: str,
    value: str,
    source: str = "inferred",
    status: str = "active",
    confidence: float = 0.6,
) -> str:
    attribute_id = str(uuid.uuid4())
    now = "2026-04-17T12:00:00+00:00"
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, 'evolving', ?, ?, 'local_only', ?, ?, ?, ?)
        """,
        (
            attribute_id,
            _domain_id(conn, domain),
            label,
            value,
            "Existing attribute.",
            source,
            confidence,
            status,
            now,
            now,
            now if status == "confirmed" else None,
        ),
    )
    conn.commit()
    return attribute_id


def _record_signal(
    conn,
    *,
    category: str = "writing_style",
    subject: str = "concise_responses",
    signal: str = "prefer",
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


def test_aggregate_and_evaluate_classify_stable_preference(conn):
    for _ in range(3):
        _record_signal(conn)

    aggregate = aggregate_signals(conn)[0]
    decision = evaluate_promotion(aggregate)

    assert aggregate.positive_count == 3
    assert aggregate.negative_count == 0
    assert aggregate.net_score == 9
    assert decision.state == "stable"
    assert decision.should_promote is True
    assert decision.domain == "voice"


def test_run_preference_promotion_creates_attribute_and_evidence(conn):
    for _ in range(3):
        _record_signal(conn)

    results = run_preference_promotion(conn)

    assert len(results) == 1
    result = results[0]
    assert result.action == "created"
    assert result.attribute_id is not None

    row = conn.execute(
        """
        SELECT d.name, label, value, source, routing, status
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE a.id = ?
        """,
        (result.attribute_id,),
    ).fetchone()
    assert row == (
        "voice",
        "preference_writing_style_concise_responses",
        "I prefer concise responses.",
        "inferred",
        "local_only",
        "active",
    )

    evidence_rows = conn.execute(
        """
        SELECT source_type, source_ref, supporting_text
        FROM inference_evidence
        WHERE attribute_id = ?
        ORDER BY source_ref
        """,
        (result.attribute_id,),
    ).fetchall()
    assert len(evidence_rows) == 3
    assert {row[0] for row in evidence_rows} == {"preference_signal"}
    assert all("3 positive and 0 negative signals" in row[2] for row in evidence_rows)


def test_run_preference_promotion_skips_insufficient_signal_clusters(conn):
    for _ in range(2):
        _record_signal(conn, category="books", subject="history", signal="like")

    results = run_preference_promotion(conn)

    assert len(results) == 1
    assert results[0].state == "emerging"
    assert results[0].action == "noop"
    count = conn.execute("SELECT count(*) FROM attributes").fetchone()[0]
    assert count == 0


def test_run_preference_promotion_skips_conflicting_signal_clusters(conn):
    for _ in range(3):
        _record_signal(conn, category="books", subject="history", signal="like")
    for _ in range(2):
        _record_signal(conn, category="books", subject="history", signal="dislike", strength=3)

    results = run_preference_promotion(conn)

    assert len(results) == 1
    assert results[0].state == "conflicting"
    assert results[0].action == "noop"
    count = conn.execute("SELECT count(*) FROM attributes").fetchone()[0]
    assert count == 0


def test_repeated_promotion_does_not_duplicate_attributes_or_evidence(conn):
    for _ in range(3):
        _record_signal(conn)

    first = run_preference_promotion(conn)[0]
    second = run_preference_promotion(conn)[0]

    assert first.action == "created"
    assert second.action == "noop"
    attribute_count = conn.execute("SELECT count(*) FROM attributes").fetchone()[0]
    evidence_count = conn.execute("SELECT count(*) FROM inference_evidence").fetchone()[0]
    assert attribute_count == 1
    assert evidence_count == 3


def test_rejected_attribute_is_not_recreated(conn):
    for _ in range(3):
        _record_signal(conn)

    rejected_id = _insert_attribute(
        conn,
        domain="voice",
        label="preference_writing_style_concise_responses",
        value="I prefer concise responses.",
        status="rejected",
    )

    results = run_preference_promotion(conn)

    assert results[0].action == "blocked_rejected"
    assert results[0].attribute_id == rejected_id
    current_count = conn.execute(
        "SELECT count(*) FROM attributes WHERE status IN ('active', 'confirmed')"
    ).fetchone()[0]
    assert current_count == 0


def test_existing_confirmed_attribute_keeps_value_and_gains_confidence(conn):
    for _ in range(4):
        _record_signal(conn)

    attribute_id = _insert_attribute(
        conn,
        domain="voice",
        label="preference_writing_style_concise_responses",
        value="I prefer short status updates with examples.",
        status="confirmed",
        confidence=0.61,
    )

    results = run_preference_promotion(conn)

    assert results[0].action == "updated"
    assert results[0].attribute_id == attribute_id

    row = conn.execute(
        "SELECT value, confidence, status FROM attributes WHERE id = ?",
        (attribute_id,),
    ).fetchone()
    assert row[0] == "I prefer short status updates with examples."
    assert row[1] > 0.61
    assert row[2] == "confirmed"

    history = conn.execute(
        """
        SELECT attribute_id, reason, changed_by
        FROM attribute_history
        WHERE attribute_id = ?
        """,
        (attribute_id,),
    ).fetchone()
    assert history == (attribute_id, "preference promotion refresh", "inferred")
