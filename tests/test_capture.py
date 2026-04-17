"""Tests for engine/capture.py and scripts/capture.py."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import ProviderConfig
from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
import engine.capture as capture_module


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


@pytest.fixture
def config():
    return ProviderConfig(
        provider="ollama",
        api_key=None,
        model="llama3.1:8b",
        is_local=True,
        arch="apple_silicon",
        ram_gb=36.0,
    )


def _domain_id(conn, name: str) -> str:
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (name,)).fetchone()
    assert row is not None
    return str(row[0])


def _insert_active(conn, domain: str, label: str, value: str, confidence: float = 0.6) -> str:
    now = "2026-04-07T12:00:00+00:00"
    attribute_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, 'stable', 'explicit', ?, 'local_only', 'active', ?, ?, ?)
        """,
        (attribute_id, _domain_id(conn, domain), label, value, None, confidence, now, now, now),
    )
    conn.commit()
    return attribute_id


def _mock_capture_response(attrs: list[dict]) -> str:
    return json.dumps(attrs)


def _mock_capture_extraction(monkeypatch, attrs: list[dict]) -> None:
    monkeypatch.setattr(
        capture_module.PrivacyBroker,
        "extract_structured_attributes",
        lambda self, messages, task_type="capture_extraction": SimpleNamespace(
            content=_mock_capture_response(attrs),
            metadata=SimpleNamespace(task_type=task_type),
        ),
    )


def test_preview_capture_uses_privacy_broker(conn, config, monkeypatch):
    calls: dict[str, object] = {}

    def _mock_extract(self, messages, task_type="capture_extraction"):
        calls["messages"] = messages
        calls["task_type"] = task_type
        return SimpleNamespace(
            content=_mock_capture_response(
                [
                    {
                        "domain": "patterns",
                        "label": "morning_focus",
                        "value": "I focus best in the morning.",
                        "elaboration": None,
                        "mutability": "evolving",
                        "confidence": 0.7,
                    }
                ]
            ),
            metadata=SimpleNamespace(task_type=task_type),
        )

    monkeypatch.setattr(
        capture_module.PrivacyBroker, "extract_structured_attributes", _mock_extract
    )

    preview = capture_module.preview_capture("I focus best in the morning.", None, config)

    assert preview[0]["label"] == "morning_focus"
    assert calls["task_type"] == "capture_extraction"
    assert isinstance(calls["messages"], list)


def test_preview_capture_with_audit_returns_privacy_safe_metadata(conn, config, monkeypatch):
    monkeypatch.setattr(
        capture_module.PrivacyBroker,
        "extract_structured_attributes",
        lambda self, messages, task_type="capture_extraction": SimpleNamespace(
            content=_mock_capture_response(
                [
                    {
                        "domain": "patterns",
                        "label": "morning_focus",
                        "value": "I focus best in the morning.",
                        "elaboration": None,
                        "mutability": "evolving",
                        "confidence": 0.7,
                    }
                ]
            ),
            metadata=SimpleNamespace(
                task_type=task_type,
                provider="ollama",
                model="llama3.1:8b",
                is_local=True,
                routing_enforced=False,
                attribute_count=0,
                domains_used=[],
                contains_local_only_context=False,
                blocked_external_attributes_count=0,
                decision="allowed",
            ),
        ),
    )

    preview = capture_module.preview_capture_with_audit(
        "I focus best in the morning.",
        None,
        config,
    )

    assert preview.content[0]["label"] == "morning_focus"
    assert preview.metadata.task_type == "capture_extraction"
    assert preview.metadata.provider == "ollama"
    assert preview.metadata.decision == "allowed"


def test_capture_non_interactive_writes_attributes(conn, config, monkeypatch):
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "patterns",
                "label": "morning_focus",
                "value": "I feel more focused in the morning.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            }
        ],
    )
    saved = capture_module.capture(
        "I focus better in the morning",
        None,
        conn,
        config,
        confirm=False,
    )
    assert len(saved) == 1
    count = conn.execute("SELECT count(*) FROM attributes").fetchone()[0]
    assert count == 1


def test_capture_sets_routing_local_only(conn, config, monkeypatch):
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "goals",
                "label": "job_search",
                "value": "I want to land a role in Seattle by end of summer.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            }
        ],
    )
    capture_module.capture("goal update", "goals", conn, config, confirm=False)
    routing = conn.execute(
        "SELECT routing FROM attributes WHERE label = 'job_search'"
    ).fetchone()[0]
    assert routing == "local_only"


def test_capture_sets_source_explicit(conn, config, monkeypatch):
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "values",
                "label": "asks_for_clarity",
                "value": "I care about naming tradeoffs clearly.",
                "elaboration": None,
                "mutability": "stable",
                "confidence": 0.7,
            }
        ],
    )
    capture_module.capture("clarity matters", None, conn, config, confirm=False)
    source = conn.execute(
        "SELECT source FROM attributes WHERE label = 'asks_for_clarity'"
    ).fetchone()[0]
    assert source == "explicit"


def test_capture_clamps_confidence_to_point_75(conn, config, monkeypatch):
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "personality",
                "label": "response_to_change",
                "value": "I adapt steadily once I understand the new shape of things.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.92,
            }
        ],
    )
    saved = capture_module.capture("I adapt steadily", None, conn, config, confirm=False)
    assert saved[0]["confidence"] == pytest.approx(0.75)
    row = conn.execute(
        "SELECT confidence FROM attributes WHERE label = 'response_to_change'"
    ).fetchone()[0]
    assert row == pytest.approx(0.75)


def test_capture_defaults_missing_confidence(conn, config, monkeypatch):
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "patterns",
                "label": "morning_focus",
                "value": "I focus best in the morning.",
                "elaboration": None,
                "mutability": "evolving",
            }
        ],
    )

    saved = capture_module.capture("Morning works well for me", None, conn, config, confirm=False)

    assert saved[0]["confidence"] == pytest.approx(0.5)


def test_capture_defaults_missing_mutability_and_elaboration(conn, config, monkeypatch):
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "values",
                "label": "clarity",
                "value": "I care about clear communication.",
                "confidence": 0.6,
            }
        ],
    )

    saved = capture_module.capture("Clarity matters to me", None, conn, config, confirm=False)

    assert saved[0]["mutability"] == "evolving"
    assert saved[0]["elaboration"] is None


def test_conflict_update_marks_old_superseded(conn, config, monkeypatch):
    old_id = _insert_active(conn, "personality", "response_to_change", "Old value")
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "personality",
                "label": "response_to_change",
                "value": "New value",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            }
        ],
    )
    responses = iter(["", "u"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    saved = capture_module.capture("update", None, conn, config, confirm=True)
    assert len(saved) == 1
    status = conn.execute("SELECT status FROM attributes WHERE id = ?", (old_id,)).fetchone()[0]
    assert status == "superseded"


def test_conflict_update_writes_attribute_history(conn, config, monkeypatch):
    old_id = _insert_active(conn, "personality", "response_to_change", "Old value")
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "personality",
                "label": "response_to_change",
                "value": "New value",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            }
        ],
    )
    responses = iter(["", "u"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    capture_module.capture("update", None, conn, config, confirm=True)
    history = conn.execute(
        "SELECT attribute_id, previous_value, reason, changed_by FROM attribute_history"
    ).fetchone()
    assert history == (old_id, "Old value", "quick capture update", "user")


def test_conflict_skip_leaves_existing_unchanged(conn, config, monkeypatch):
    old_id = _insert_active(conn, "patterns", "asks_for_help", "I avoid asking for help.")
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "patterns",
                "label": "asks_for_help",
                "value": "I ask for help earlier now.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.6,
            }
        ],
    )
    responses = iter(["", "s"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    saved = capture_module.capture("pattern update", None, conn, config, confirm=True)
    assert saved == []
    row = conn.execute(
        "SELECT id, value, status FROM attributes WHERE label = 'asks_for_help'"
    ).fetchone()
    assert row == (old_id, "I avoid asking for help.", "active")


def test_conflict_keep_both_writes_with_suffix(conn, config, monkeypatch):
    _insert_active(conn, "patterns", "asks_for_help", "I avoid asking for help.")
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "patterns",
                "label": "asks_for_help",
                "value": "I am getting better at asking for help.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.65,
            }
        ],
    )
    responses = iter(["", "k"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    saved = capture_module.capture("pattern update", None, conn, config, confirm=True)
    assert saved[0]["label"] == "asks_for_help_2"
    count = conn.execute(
        "SELECT count(*) FROM attributes WHERE label = 'asks_for_help_2' AND status = 'active'"
    ).fetchone()[0]
    assert count == 1


def test_conflict_keep_both_uses_next_free_suffix(conn, config, monkeypatch):
    _insert_active(conn, "patterns", "asks_for_help", "v1")
    _insert_active(conn, "patterns", "asks_for_help_2", "v2")
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "patterns",
                "label": "asks_for_help",
                "value": "v3",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.65,
            }
        ],
    )
    responses = iter(["", "k"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    saved = capture_module.capture("pattern update", None, conn, config, confirm=True)
    assert saved[0]["label"] == "asks_for_help_3"


def test_non_interactive_conflict_defaults_to_skip(conn, config, monkeypatch, caplog):
    _insert_active(conn, "goals", "job_search", "Existing goal")
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "goals",
                "label": "job_search",
                "value": "New goal",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            }
        ],
    )
    saved = capture_module.capture("goal update", None, conn, config, confirm=False)
    assert saved == []
    assert "Skipping quick capture conflict" in caplog.text
    count = conn.execute("SELECT count(*) FROM attributes WHERE label = 'job_search'").fetchone()[0]
    assert count == 1


def test_capture_returns_only_written_attributes(conn, config, monkeypatch):
    _insert_active(conn, "goals", "job_search", "Existing goal")
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "goals",
                "label": "job_search",
                "value": "Conflicting goal",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            },
            {
                "domain": "patterns",
                "label": "morning_focus",
                "value": "I think more clearly in the morning.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            },
        ],
    )
    saved = capture_module.capture("mixed capture", None, conn, config, confirm=False)
    assert [attr["label"] for attr in saved] == ["morning_focus"]


def test_invalid_domain_name_raises_clear_error(conn, config, monkeypatch):
    _mock_capture_extraction(monkeypatch, [])
    with pytest.raises(ValueError, match="Invalid domain hint 'not-a-domain'"):
        capture_module.capture("text", "not-a-domain", conn, config, confirm=False)


def test_capture_does_not_write_reflection_session(conn, config, monkeypatch):
    _mock_capture_extraction(monkeypatch, [])
    capture_module.capture("text", None, conn, config, confirm=False)
    count = conn.execute("SELECT count(*) FROM reflection_sessions").fetchone()[0]
    assert count == 0
