"""Focused tests for passive session-learning staging."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import ProviderConfig
from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.privacy_broker import BrokeredResult, InferenceDecision
from engine.session import Session
from engine.session_learner import maybe_extract_from_exchange


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


@pytest.fixture
def local_config():
    return ProviderConfig(
        provider="ollama",
        api_key=None,
        model="llama3.1:8b",
        is_local=True,
        arch="apple_silicon",
        ram_gb=36.0,
    )


def _audit() -> InferenceDecision:
    return InferenceDecision(
        provider="ollama",
        model="llama3.1:8b",
        is_local=True,
        task_type="session_learning_signal_extraction",
        blocked_external_attributes_count=0,
        routing_enforced=False,
    )


def test_maybe_extract_from_exchange_stages_attribute_and_preference_signals(
    conn,
    local_config,
    monkeypatch,
):
    session = Session()
    user_query = (
        "I usually do my best work alone in the morning, and I get overwhelmed in long meetings "
        "when there are too many rapid context switches happening at once."
    )
    session.add_exchange(user_query, "Thanks, that helps.")
    session.query_count = 1

    monkeypatch.setattr(
        "engine.session_learner.resolve_local_provider_config",
        lambda provider_config: provider_config,
    )

    responses = iter(
        [
            BrokeredResult(
                content=json.dumps(
                    {
                        "attribute_candidates": [
                            {
                                "domain": "patterns",
                                "label": "meeting_overwhelm",
                                "value": "I get overwhelmed in long meetings with rapid context switching.",
                                "elaboration": "Solo morning work feels much easier to sustain.",
                                "mutability": "evolving",
                                "confidence": 0.72,
                            }
                        ],
                        "preference_signals": [
                            {
                                "category": "work_style",
                                "subject": "solo_morning_work",
                                "signal": "prefer",
                                "strength": 4,
                                "summary": "The user describes doing their best work alone in the morning.",
                            }
                        ],
                    }
                ),
                metadata=_audit(),
            )
        ]
    )

    monkeypatch.setattr(
        "engine.session_learner.PrivacyBroker.extract_structured_attributes",
        lambda self, messages, **kwargs: next(responses),
    )

    staged = maybe_extract_from_exchange(
        conn,
        session,
        user_query=user_query,
        coverage_confidence="low_confidence",
        retrieved_attributes=[],
        provider_config=local_config,
        source_profile="self_question",
        domain_hints=["patterns"],
    )

    rows = conn.execute(
        """
        SELECT signal_type, payload_json, processed, session_id, exchange_index
        FROM extracted_session_signals
        ORDER BY signal_type ASC
        """
    ).fetchall()

    assert staged == 2
    assert len(rows) == 2
    assert {row[0] for row in rows} == {"attribute_candidate", "preference"}
    assert all(row[2] == 0 for row in rows)
    assert all(row[3] == session.id for row in rows)
    assert all(row[4] == 0 for row in rows)

    attribute_payload = json.loads(next(row[1] for row in rows if row[0] == "attribute_candidate"))
    preference_payload = json.loads(next(row[1] for row in rows if row[0] == "preference"))

    assert attribute_payload["domain"] == "patterns"
    assert attribute_payload["label"] == "meeting_overwhelm"
    assert attribute_payload["source_profile"] == "self_question"
    assert attribute_payload["domain_hints"] == ["patterns"]
    assert "overwhelmed in long meetings" in attribute_payload["query_excerpt"]

    assert preference_payload["category"] == "work_style"
    assert preference_payload["subject"] == "solo_morning_work"
    assert preference_payload["signal"] == "prefer"
    assert preference_payload["strength"] == 4


def test_maybe_extract_from_exchange_stages_corrections_with_linked_attribute_ids(
    conn,
    local_config,
    monkeypatch,
):
    session = Session()
    session.add_exchange("You said I avoid conflict.", "I think conflict avoidance shows up for you.")
    user_query = (
        "Actually, I do not avoid conflict everywhere. I push back quickly when the stakes are high, "
        "and the real issue is that I need time to think before responding in low-stakes situations."
    )
    session.add_exchange(user_query, "That distinction makes sense.")
    session.query_count = 2

    monkeypatch.setattr(
        "engine.session_learner.resolve_local_provider_config",
        lambda provider_config: provider_config,
    )

    responses = iter(
        [
            BrokeredResult(
                content=json.dumps({"attribute_candidates": [], "preference_signals": []}),
                metadata=_audit(),
            ),
            BrokeredResult(
                content=json.dumps(
                    [
                        {
                            "summary": "The user corrected a blanket conflict-avoidance framing.",
                            "correction_text": "Conflict avoidance depends on the stakes and response time.",
                            "confidence": 0.78,
                            "attribute_ids": ["attr-1", "missing-id"],
                        }
                    ]
                ),
                metadata=_audit(),
            ),
        ]
    )

    monkeypatch.setattr(
        "engine.session_learner.PrivacyBroker.extract_structured_attributes",
        lambda self, messages, **kwargs: next(responses),
    )

    staged = maybe_extract_from_exchange(
        conn,
        session,
        user_query=user_query,
        coverage_confidence="medium_confidence",
        retrieved_attributes=[
            {
                "id": "attr-1",
                "domain": "patterns",
                "label": "conflict_style",
                "value": "I avoid conflict when conversations feel emotionally charged.",
            }
        ],
        provider_config=local_config,
        source_profile="self_question",
        domain_hints=["patterns"],
    )

    row = conn.execute(
        """
        SELECT signal_type, payload_json, exchange_index
        FROM extracted_session_signals
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()

    assert staged == 1
    assert row[0] == "correction"
    assert row[2] == 1

    payload = json.loads(row[1])
    assert payload["attribute_ids"] == ["attr-1"]
    assert payload["matched_phrases"] == ["actually"]
    assert payload["summary"] == "The user corrected a blanket conflict-avoidance framing."
    assert payload["source_profile"] == "self_question"


def test_maybe_extract_from_exchange_skips_high_confidence_queries(
    conn,
    local_config,
    monkeypatch,
):
    session = Session()
    user_query = (
        "I am describing myself in detail here, but this turn already has plenty of confirmed grounded "
        "context and should not trigger passive learning."
    )
    session.add_exchange(user_query, "Already grounded.")
    session.query_count = 1

    extract_calls: list[object] = []

    monkeypatch.setattr(
        "engine.session_learner.PrivacyBroker.extract_structured_attributes",
        lambda self, messages, **kwargs: extract_calls.append(messages),
    )

    staged = maybe_extract_from_exchange(
        conn,
        session,
        user_query=user_query,
        coverage_confidence="high_confidence",
        retrieved_attributes=[],
        provider_config=local_config,
        source_profile="self_question",
        domain_hints=["personality"],
    )

    assert staged == 0
    assert extract_calls == []
    count = conn.execute("SELECT COUNT(*) FROM extracted_session_signals").fetchone()[0]
    assert count == 0
