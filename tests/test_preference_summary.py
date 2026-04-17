"""Tests for runtime preference summaries and deterministic ranking."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.preference_signals import PreferenceSignalInput, record_preference_signal
from db.schema import create_tables, seed_domains
from engine.preference_ranker import rank_candidates, score_candidate_against_preferences
from engine.preference_summary import PreferenceSummaryPayload, get_relevant_preference_context


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


def _insert_preference_attribute(
    conn,
    *,
    domain: str = "voice",
    label: str = "preference_writing_style_concise_responses",
    value: str = "I prefer concise responses.",
    status: str = "confirmed",
    routing: str = "local_only",
    confidence: float = 0.88,
) -> None:
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status
        )
        VALUES (?, ?, ?, ?, ?, 'evolving', 'inferred', ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            _domain_id(conn, domain),
            label,
            value,
            "Preference attribute.",
            confidence,
            routing,
            status,
        ),
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


def test_preference_summary_prioritizes_confirmed_attribute_over_duplicate_signal(conn):
    _insert_preference_attribute(conn)
    for _ in range(3):
        _record_signal(
            conn,
            category="writing_style",
            subject="concise_responses",
            signal="prefer",
            strength=4,
        )

    result = get_relevant_preference_context("Help me draft a short email.", "simple", conn)

    positive = list(result.summary["positive"])
    assert len(result.attributes) == 1
    assert any(item["source"] == "attribute" for item in positive)
    assert not any(item["source"] == "signal_summary" for item in positive)


def test_preference_summary_surfaces_negative_relevant_signals(conn):
    for _ in range(3):
        _record_signal(
            conn,
            category="writing_style",
            subject="dense_long_form",
            signal="avoid",
            strength=4,
        )

    result = get_relevant_preference_context(
        "Rewrite this update so it sounds better.",
        "simple",
        conn,
    )

    negative = list(result.summary["negative"])
    assert len(negative) == 1
    assert negative[0]["source"] == "signal_summary"
    assert "dense long form" in negative[0]["summary"].lower()


def test_preference_summary_excludes_irrelevant_categories(conn):
    for _ in range(3):
        _record_signal(
            conn,
            category="books",
            subject="history",
            signal="like",
            strength=4,
        )

    result = get_relevant_preference_context("What are my values?", "simple", conn)

    assert result.attributes == []
    assert result.summary["positive"] == []
    assert result.summary["negative"] == []


def test_preference_summary_handles_empty_state_cleanly(conn):
    result = get_relevant_preference_context("Help me plan tomorrow.", "simple", conn)

    assert result.attributes == []
    assert result.item_count == 0
    assert result.summary["positive"] == []
    assert result.summary["negative"] == []


def test_preference_ranker_prefers_positive_matches_and_penalizes_avoids():
    summary: PreferenceSummaryPayload = {
        "task_profiles": [],
        "positive": [
            {
                "category": "voice",
                "subject": "concise_responses",
                "summary": "I prefer concise responses.",
                "source": "attribute",
                "status": "confirmed",
                "direction": "positive",
                "confidence": 0.9,
            }
        ],
        "negative": [
            {
                "category": "writing_style",
                "subject": "dense_long_form",
                "summary": "Avoid dense long form content.",
                "source": "signal_summary",
                "status": "summary",
                "direction": "negative",
                "confidence": None,
            }
        ],
    }

    concise = score_candidate_against_preferences(
        {
            "name": "Concise update",
            "category": "writing",
            "tags": ["concise", "responses"],
        },
        summary,
    )
    dense = score_candidate_against_preferences(
        {
            "name": "Long memo",
            "category": "writing",
            "tags": ["dense", "long", "form"],
        },
        summary,
    )

    assert concise["score"] > dense["score"]
    assert any("positive preference" in reason for reason in concise["reasons"])
    assert any("avoid preference" in reason for reason in dense["reasons"])


def test_rank_candidates_is_deterministic():
    summary: PreferenceSummaryPayload = {
        "task_profiles": [],
        "positive": [
            {
                "category": "books",
                "subject": "history",
                "summary": "Prefer history books.",
                "source": "signal_summary",
                "status": "summary",
                "direction": "positive",
                "confidence": None,
            }
        ],
        "negative": [],
    }

    ranked = rank_candidates(
        [
            {"name": "History pick", "category": "books", "tags": ["history"]},
            {"name": "Sci-fi pick", "category": "books", "tags": ["science", "fiction"]},
        ],
        summary,
    )

    assert [item["label"] for item in ranked] == ["History pick", "Sci-fi pick"]
