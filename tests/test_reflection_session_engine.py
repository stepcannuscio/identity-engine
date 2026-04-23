"""Tests for engine/reflection_session_engine.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.reflection_session_engine import (
    ReflectionSeed,
    ReflectionSessionState,
    SuggestedAttributeUpdate,
    _build_seed_question,
    _fallback_question,
    _parse_reflection_response,
    _stage_reflection_signal,
    _valid_update,
    build_reflection_session_seed,
    process_reflection_turn,
    start_reflection_session,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


@pytest.fixture
def domain_id(conn):
    row = conn.execute("SELECT id FROM domains WHERE name = 'personality' LIMIT 1").fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture
def attr_id(conn, domain_id):
    import uuid
    from datetime import UTC, datetime

    aid = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO attributes (id, domain_id, label, value, mutability, source,
                                confidence, routing, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (aid, domain_id, "introversion", "I recharge alone", "stable", "explicit", 0.9,
         "local_only", "confirmed", now, now),
    )
    conn.commit()
    return aid


# ---------------------------------------------------------------------------
# build_reflection_session_seed
# ---------------------------------------------------------------------------


def test_seed_falls_back_to_most_populated_domain(conn, attr_id):
    seed = build_reflection_session_seed(conn)
    assert seed.seed_domain == "personality"


def test_seed_prefers_contradiction_domain(conn, domain_id):
    import uuid
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()

    def _insert_attr(label, value, domain_id_=domain_id):
        aid = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO attributes (id, domain_id, label, value, mutability, source,
                                    confidence, routing, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (aid, domain_id_, label, value, "stable", "explicit", 0.85,
             "local_only", "confirmed", now, now),
        )
        conn.commit()
        return aid

    a_id = _insert_attr("stability", "I need stability")
    b_id = _insert_attr("change_seeking", "I love constant change")
    cf_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO contradiction_flags
            (id, attribute_a_id, attribute_b_id, polarity_axis, confidence, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cf_id, a_id, b_id, "stability_change", 0.8, "pending", now),
    )
    conn.commit()

    seed = build_reflection_session_seed(conn)
    assert seed.seed_domain == "personality"
    assert len(seed.pending_contradictions) == 1
    assert "I need stability" in seed.seed_question


def test_seed_question_no_data():
    q = _build_seed_question(None, [], [], [])
    assert "important" in q.lower()


def test_seed_question_with_domain():
    q = _build_seed_question("goals", [], [], [])
    assert "goals" in q


def test_seed_question_with_drift():
    q = _build_seed_question("values", [], [], ["values"])
    assert "values" in q


def test_seed_question_with_contradiction():
    contradiction = [{"attribute_a_value": "I love routine", "attribute_b_value": "I seek novelty"}]
    q = _build_seed_question("personality", contradiction, [], [])
    assert "routine" in q
    assert "novelty" in q


# ---------------------------------------------------------------------------
# _parse_reflection_response
# ---------------------------------------------------------------------------


def test_parse_valid_json():
    raw = '{"next_question": "What drives you?", "suggested_attribute_updates": [], "themes_noticed": []}'
    result = _parse_reflection_response(raw)
    assert result is not None
    assert result["next_question"] == "What drives you?"


def test_parse_json_wrapped_in_prose():
    raw = 'Here is the JSON: {"next_question": "How are you?", "suggested_attribute_updates": [], "themes_noticed": ["resilience"]}'
    result = _parse_reflection_response(raw)
    assert result is not None
    assert result["next_question"] == "How are you?"
    assert "resilience" in result["themes_noticed"]


def test_parse_invalid_returns_none():
    assert _parse_reflection_response("not json at all") is None


def test_parse_empty_returns_none():
    assert _parse_reflection_response("") is None


# ---------------------------------------------------------------------------
# _valid_update
# ---------------------------------------------------------------------------


def test_valid_update_accepts_complete_dict():
    assert _valid_update({"domain": "goals", "label": "focus", "value": "deep work"}) is True


def test_valid_update_rejects_missing_fields():
    assert _valid_update({"domain": "goals", "label": "focus"}) is False
    assert _valid_update({"domain": "goals", "value": "deep work"}) is False
    assert _valid_update({}) is False
    assert _valid_update("not a dict") is False


# ---------------------------------------------------------------------------
# _fallback_question
# ---------------------------------------------------------------------------


def test_fallback_question_sequences():
    state = ReflectionSessionState(
        session_id="s1", history=[], domains_explored=["goals"],
        themes_noticed=[], seed_domain="goals", turn_count=1,
        started_at="", staged_signal_ids=[],
    )
    q1 = _fallback_question(state)
    assert "goals" in q1

    state.turn_count = 2
    q2 = _fallback_question(state)
    assert "changed" in q2.lower()

    state.turn_count = 3
    q3 = _fallback_question(state)
    assert "future" in q3.lower()

    state.turn_count = 10
    q4 = _fallback_question(state)
    assert q4


# ---------------------------------------------------------------------------
# _stage_reflection_signal
# ---------------------------------------------------------------------------


def test_stage_signal_writes_to_db(conn):
    import uuid

    session_id = str(uuid.uuid4())
    update = SuggestedAttributeUpdate(
        domain="goals",
        label="focus_preference",
        value="deep focused work",
        confidence=0.6,
        elaboration="I prefer long uninterrupted blocks",
    )
    signal_id = _stage_reflection_signal(conn, session_id, update)
    assert signal_id is not None

    row = conn.execute(
        "SELECT signal_type, payload_json, processed FROM extracted_session_signals WHERE id = ?",
        (signal_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "attribute_candidate"
    assert row[2] == 0

    payload = json.loads(row[1])
    assert payload["domain"] == "goals"
    assert payload["label"] == "focus_preference"
    assert payload["elaboration"] == "I prefer long uninterrupted blocks"


def test_stage_signal_without_elaboration(conn):
    import uuid

    session_id = str(uuid.uuid4())
    update = SuggestedAttributeUpdate(
        domain="personality", label="introversion", value="I prefer solitude",
        confidence=0.5, elaboration=None,
    )
    signal_id = _stage_reflection_signal(conn, session_id, update)
    assert signal_id is not None
    row = conn.execute(
        "SELECT payload_json FROM extracted_session_signals WHERE id = ?",
        (signal_id,),
    ).fetchone()
    payload = json.loads(row[0])
    assert "elaboration" not in payload


# ---------------------------------------------------------------------------
# start_reflection_session
# ---------------------------------------------------------------------------


def _make_provider_config():
    config = MagicMock()
    config.is_local = True
    return config


def test_start_session_llm_unavailable_falls_back(conn, attr_id):
    provider_config = _make_provider_config()
    with patch("engine.reflection_session_engine.PrivacyBroker") as mock_broker_cls:
        mock_broker_cls.return_value.generate_grounded_response.side_effect = RuntimeError("no model")
        session_id, state, first_question = start_reflection_session(conn, provider_config)

    assert session_id
    assert first_question
    assert state.turn_count == 1
    assert state.history[0]["role"] == "assistant"
    assert state.history[0]["content"] == first_question


def test_start_session_uses_llm_question_when_available(conn, attr_id):
    provider_config = _make_provider_config()
    mock_result = MagicMock()
    mock_result.content = json.dumps({
        "next_question": "LLM-generated first question",
        "suggested_attribute_updates": [],
        "themes_noticed": [],
    })
    with patch("engine.reflection_session_engine.PrivacyBroker") as mock_broker_cls:
        mock_broker_cls.return_value.generate_grounded_response.return_value = mock_result
        session_id, state, first_question = start_reflection_session(conn, provider_config)

    assert first_question == "LLM-generated first question"


# ---------------------------------------------------------------------------
# process_reflection_turn
# ---------------------------------------------------------------------------


def test_process_turn_llm_unavailable_uses_fallback(conn):
    import uuid

    session_id = str(uuid.uuid4())
    state = ReflectionSessionState(
        session_id=session_id,
        history=[{"role": "assistant", "content": "What motivates you?"}],
        domains_explored=["goals"],
        themes_noticed=[],
        seed_domain="goals",
        turn_count=1,
        started_at="",
        staged_signal_ids=[],
    )
    provider_config = _make_provider_config()
    with patch("engine.reflection_session_engine.PrivacyBroker") as mock_broker_cls:
        mock_broker_cls.return_value.generate_grounded_response.side_effect = RuntimeError("no model")
        result = process_reflection_turn(conn, state, "Deep work energizes me.", provider_config)

    assert result.next_question
    assert state.turn_count == 2
    assert state.history[-1]["role"] == "assistant"
    assert state.history[-2]["role"] == "user"
    assert state.history[-2]["content"] == "Deep work energizes me."


def test_process_turn_stages_llm_suggestions(conn):
    import uuid

    session_id = str(uuid.uuid4())
    state = ReflectionSessionState(
        session_id=session_id,
        history=[{"role": "assistant", "content": "What energizes you?"}],
        domains_explored=["goals"],
        themes_noticed=[],
        seed_domain="goals",
        turn_count=1,
        started_at="",
        staged_signal_ids=[],
    )
    provider_config = _make_provider_config()
    mock_result = MagicMock()
    mock_result.content = json.dumps({
        "next_question": "How does that show up in your work?",
        "suggested_attribute_updates": [
            {"domain": "goals", "label": "energy_source", "value": "deep focused work",
             "confidence": 0.6, "elaboration": None},
        ],
        "themes_noticed": ["flow state"],
    })
    with patch("engine.reflection_session_engine.PrivacyBroker") as mock_broker_cls:
        mock_broker_cls.return_value.generate_grounded_response.return_value = mock_result
        result = process_reflection_turn(conn, state, "Deep work energizes me.", provider_config)

    assert result.next_question == "How does that show up in your work?"
    assert len(result.suggested_updates) == 1
    assert result.suggested_updates[0].label == "energy_source"
    assert result.themes_noticed == ["flow state"]
    assert len(result.staged_signal_ids) == 1
    assert "flow state" in state.themes_noticed

    # Confirm signal is in DB
    row = conn.execute(
        "SELECT signal_type FROM extracted_session_signals WHERE id = ?",
        (result.staged_signal_ids[0],),
    ).fetchone()
    assert row is not None
    assert row[0] == "attribute_candidate"


def test_process_turn_caps_confidence(conn):
    import uuid

    session_id = str(uuid.uuid4())
    state = ReflectionSessionState(
        session_id=session_id,
        history=[{"role": "assistant", "content": "First question"}],
        domains_explored=[],
        themes_noticed=[],
        seed_domain=None,
        turn_count=1,
        started_at="",
        staged_signal_ids=[],
    )
    provider_config = _make_provider_config()
    mock_result = MagicMock()
    mock_result.content = json.dumps({
        "next_question": "Next?",
        "suggested_attribute_updates": [
            {"domain": "personality", "label": "core_trait", "value": "resilient",
             "confidence": 0.99, "elaboration": None},
        ],
        "themes_noticed": [],
    })
    with patch("engine.reflection_session_engine.PrivacyBroker") as mock_broker_cls:
        mock_broker_cls.return_value.generate_grounded_response.return_value = mock_result
        result = process_reflection_turn(conn, state, "I bounce back quickly.", provider_config)

    assert result.suggested_updates[0].confidence <= 0.75


def test_process_turn_deduplicates_themes(conn):
    import uuid

    session_id = str(uuid.uuid4())
    state = ReflectionSessionState(
        session_id=session_id,
        history=[{"role": "assistant", "content": "What drives you?"}],
        domains_explored=[],
        themes_noticed=["resilience"],
        seed_domain=None,
        turn_count=1,
        started_at="",
        staged_signal_ids=[],
    )
    provider_config = _make_provider_config()
    mock_result = MagicMock()
    mock_result.content = json.dumps({
        "next_question": "What else?",
        "suggested_attribute_updates": [],
        "themes_noticed": ["resilience", "growth"],
    })
    with patch("engine.reflection_session_engine.PrivacyBroker") as mock_broker_cls:
        mock_broker_cls.return_value.generate_grounded_response.return_value = mock_result
        result = process_reflection_turn(conn, state, "I keep going.", provider_config)

    assert "resilience" not in result.themes_noticed
    assert "growth" in result.themes_noticed
    assert state.themes_noticed.count("resilience") == 1
