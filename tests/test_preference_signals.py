"""Tests for preference-signal persistence helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.preference_signals import (
    PreferenceSignalInput,
    list_preference_signals,
    record_preference_signal,
    summarize_preference_signals,
)
from db.schema import create_tables, seed_domains


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


def test_record_preference_signal_persists_signal(conn):
    record = record_preference_signal(
        conn,
        PreferenceSignalInput(
            category="writing_style",
            subject="concise_responses",
            signal="prefer",
            strength=4,
            source="explicit_feedback",
            context={"time_of_day": "morning"},
        ),
    )

    assert record.category == "writing_style"
    assert record.subject == "concise_responses"
    assert record.context == {"time_of_day": "morning"}


def test_list_preference_signals_filters_by_category_and_subject(conn):
    record_preference_signal(
        conn,
        PreferenceSignalInput(
            category="writing_style",
            subject="concise_responses",
            signal="prefer",
        ),
    )
    record_preference_signal(
        conn,
        PreferenceSignalInput(
            category="books",
            subject="history",
            signal="like",
        ),
    )

    writing = list_preference_signals(conn, category="writing_style")
    history = list_preference_signals(conn, subject="history")

    assert len(writing) == 1
    assert writing[0].subject == "concise_responses"
    assert len(history) == 1
    assert history[0].category == "books"


def test_summarize_preference_signals_returns_simple_net_scores(conn):
    record_preference_signal(
        conn,
        PreferenceSignalInput(
            category="writing_style",
            subject="concise_responses",
            signal="prefer",
            strength=4,
        ),
    )
    record_preference_signal(
        conn,
        PreferenceSignalInput(
            category="writing_style",
            subject="concise_responses",
            signal="avoid",
            strength=2,
        ),
    )

    summaries = summarize_preference_signals(conn, category="writing_style")

    assert len(summaries) == 1
    assert summaries[0].observations == 2
    assert summaries[0].positive_count == 1
    assert summaries[0].negative_count == 1
    assert summaries[0].net_score == 2


def test_list_and_summary_handle_empty_results(conn):
    assert list_preference_signals(conn, category="planning") == []
    assert summarize_preference_signals(conn, category="planning") == []


def test_record_preference_signal_rejects_missing_attribute_reference(conn):
    with pytest.raises(ValueError, match="attribute_id"):
        record_preference_signal(
            conn,
            PreferenceSignalInput(
                category="writing_style",
                subject="concise_responses",
                signal="prefer",
                attribute_id="missing-attribute",
            ),
        )
