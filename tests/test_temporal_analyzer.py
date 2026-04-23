"""Tests for Phase 5 temporal intelligence analysis."""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.temporal_analyzer import (
    _DECAY_DAYS,
    _DECAY_MIN_CONFIDENCE,
    _DRIFT_MIN_CHANGES,
    _DRIFT_WINDOW_DAYS,
    _SHIFT_MIN_ATTRIBUTES,
    _SHIFT_WINDOW_DAYS,
    list_active_temporal_events,
    list_all_temporal_events,
    refresh_temporal_intelligence,
)


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as connection:
        create_tables(connection)
        seed_domains(connection)
        yield connection


def _domain_id(conn, name: str) -> str:
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (name,)).fetchone()
    assert row is not None, f"Domain '{name}' not found"
    return str(row[0])


def _insert_attribute(
    conn,
    *,
    domain: str,
    label: str,
    value: str,
    confidence: float = 0.80,
    status: str = "confirmed",
    last_confirmed: str | None = None,
    updated_at: str | None = None,
) -> str:
    did = _domain_id(conn, domain)
    attr_id = str(uuid.uuid4())
    now = updated_at or datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source,
            confidence, routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, NULL, 'stable', 'explicit', ?, 'local_only', ?, ?, ?, ?)
        """,
        (attr_id, did, label, value, confidence, status, now, now, last_confirmed),
    )
    conn.commit()
    return attr_id


def _insert_history(conn, attribute_id: str, changed_at: str, previous_value: str = "old") -> None:
    conn.execute(
        """
        INSERT INTO attribute_history (id, attribute_id, previous_value, previous_confidence, reason, changed_at, changed_by)
        VALUES (?, ?, ?, 0.5, 'test', ?, 'user')
        """,
        (str(uuid.uuid4()), attribute_id, previous_value, changed_at),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def test_detect_drift_stages_event_when_attribute_changed_twice(conn):
    attr_id = _insert_attribute(conn, domain="personality", label="openness", value="high")
    now = datetime.now(UTC)
    _insert_history(conn, attr_id, (now - timedelta(days=10)).isoformat())
    _insert_history(conn, attr_id, (now - timedelta(days=5)).isoformat())

    events = refresh_temporal_intelligence(conn)
    drift_events = [e for e in events if e.event_type == "drift"]
    assert len(drift_events) == 1
    assert attr_id in drift_events[0].attribute_ids
    assert drift_events[0].domain == "personality"


def test_detect_drift_ignores_single_change(conn):
    attr_id = _insert_attribute(conn, domain="values", label="honesty", value="core")
    _insert_history(conn, attr_id, datetime.now(UTC).isoformat())

    events = refresh_temporal_intelligence(conn)
    drift_events = [e for e in events if e.event_type == "drift"]
    assert not drift_events


def test_detect_drift_ignores_old_changes(conn):
    attr_id = _insert_attribute(conn, domain="goals", label="career", value="lead")
    old = (datetime.now(UTC) - timedelta(days=_DRIFT_WINDOW_DAYS + 10)).isoformat()
    _insert_history(conn, attr_id, old, "earlier")
    _insert_history(conn, attr_id, (datetime.now(UTC) - timedelta(days=_DRIFT_WINDOW_DAYS + 5)).isoformat())

    events = refresh_temporal_intelligence(conn)
    drift_events = [e for e in events if e.event_type == "drift"]
    assert not drift_events


def test_detect_drift_deduplicates_on_second_run(conn):
    attr_id = _insert_attribute(conn, domain="personality", label="openness", value="high")
    now = datetime.now(UTC)
    _insert_history(conn, attr_id, (now - timedelta(days=10)).isoformat())
    _insert_history(conn, attr_id, (now - timedelta(days=5)).isoformat())

    events1 = refresh_temporal_intelligence(conn)
    events2 = refresh_temporal_intelligence(conn)
    drift1 = [e for e in events1 if e.event_type == "drift"]
    drift2 = [e for e in events2 if e.event_type == "drift"]
    assert len(drift1) == 1
    assert len(drift2) == 1
    assert drift1[0].id == drift2[0].id


# ---------------------------------------------------------------------------
# Shift cluster detection
# ---------------------------------------------------------------------------

def test_detect_shift_cluster_stages_event_for_domain_transition(conn):
    now = datetime.now(UTC)
    for label in ("trait_a", "trait_b", "trait_c"):
        attr_id = _insert_attribute(conn, domain="personality", label=label, value="v")
        _insert_history(conn, attr_id, (now - timedelta(days=30)).isoformat())

    events = refresh_temporal_intelligence(conn)
    shift_events = [e for e in events if e.event_type == "shift_cluster"]
    assert len(shift_events) == 1
    assert shift_events[0].domain == "personality"
    assert len(shift_events[0].attribute_ids) >= _SHIFT_MIN_ATTRIBUTES


def test_detect_shift_cluster_ignores_sparse_changes(conn):
    now = datetime.now(UTC)
    for i, label in enumerate(("goal_a", "goal_b", "goal_c")):
        attr_id = _insert_attribute(conn, domain="goals", label=label, value="v")
        # Spread changes 100 days apart — outside the 90-day window
        _insert_history(conn, attr_id, (now - timedelta(days=i * 100)).isoformat())

    events = refresh_temporal_intelligence(conn)
    shift_events = [e for e in events if e.event_type == "shift_cluster"]
    assert not shift_events


def test_detect_shift_cluster_deduplicates_on_second_run(conn):
    now = datetime.now(UTC)
    for label in ("a", "b", "c"):
        attr_id = _insert_attribute(conn, domain="values", label=label, value="v")
        _insert_history(conn, attr_id, (now - timedelta(days=5)).isoformat())

    events1 = refresh_temporal_intelligence(conn)
    events2 = refresh_temporal_intelligence(conn)
    shifts1 = [e for e in events1 if e.event_type == "shift_cluster"]
    shifts2 = [e for e in events2 if e.event_type == "shift_cluster"]
    assert len(shifts1) == 1
    assert len(shifts2) == 1
    assert shifts1[0].id == shifts2[0].id


# ---------------------------------------------------------------------------
# Confidence decay detection
# ---------------------------------------------------------------------------

def test_detect_confidence_decay_stages_event_for_stale_attribute(conn):
    stale_date = (datetime.now(UTC) - timedelta(days=_DECAY_DAYS + 30)).isoformat()
    attr_id = _insert_attribute(
        conn,
        domain="beliefs",
        label="self_worth",
        value="high intrinsic",
        confidence=0.85,
        last_confirmed=stale_date,
        updated_at=stale_date,
    )

    events = refresh_temporal_intelligence(conn)
    decay_events = [e for e in events if e.event_type == "confidence_decay"]
    assert len(decay_events) == 1
    assert attr_id in decay_events[0].attribute_ids
    assert "self_worth" in (decay_events[0].description or "")


def test_detect_confidence_decay_skips_recently_confirmed(conn):
    recent = datetime.now(UTC).isoformat()
    _insert_attribute(
        conn,
        domain="beliefs",
        label="self_worth",
        value="high",
        confidence=0.85,
        last_confirmed=recent,
        updated_at=recent,
    )

    events = refresh_temporal_intelligence(conn)
    decay_events = [e for e in events if e.event_type == "confidence_decay"]
    assert not decay_events


def test_detect_confidence_decay_skips_low_confidence(conn):
    stale = (datetime.now(UTC) - timedelta(days=_DECAY_DAYS + 30)).isoformat()
    _insert_attribute(
        conn,
        domain="beliefs",
        label="uncertain_belief",
        value="maybe",
        confidence=0.50,
        last_confirmed=stale,
        updated_at=stale,
    )

    events = refresh_temporal_intelligence(conn)
    decay_events = [e for e in events if e.event_type == "confidence_decay"]
    assert not decay_events


def test_detect_confidence_decay_auto_resolves_when_confirmed(conn):
    stale = (datetime.now(UTC) - timedelta(days=_DECAY_DAYS + 30)).isoformat()
    attr_id = _insert_attribute(
        conn,
        domain="beliefs",
        label="self_worth",
        value="high",
        confidence=0.85,
        last_confirmed=stale,
        updated_at=stale,
    )
    # First run stages the decay event
    events1 = refresh_temporal_intelligence(conn)
    assert any(e.event_type == "confidence_decay" for e in events1)

    # Simulate user confirming the attribute
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE attributes SET last_confirmed = ?, updated_at = ? WHERE id = ?",
        (now, now, attr_id),
    )
    conn.commit()

    # Second run should auto-resolve
    events2 = refresh_temporal_intelligence(conn)
    decay_active = [e for e in events2 if e.event_type == "confidence_decay"]
    assert not decay_active

    # Resolved event should appear in full history
    all_events = list_all_temporal_events(conn)
    resolved = [e for e in all_events if e.event_type == "confidence_decay" and e.status == "resolved"]
    assert len(resolved) == 1


def test_detect_confidence_decay_deduplicates_on_second_run(conn):
    stale = (datetime.now(UTC) - timedelta(days=_DECAY_DAYS + 30)).isoformat()
    _insert_attribute(
        conn,
        domain="beliefs",
        label="resilience",
        value="strong",
        confidence=0.80,
        last_confirmed=stale,
        updated_at=stale,
    )

    events1 = refresh_temporal_intelligence(conn)
    events2 = refresh_temporal_intelligence(conn)
    decay1 = [e for e in events1 if e.event_type == "confidence_decay"]
    decay2 = [e for e in events2 if e.event_type == "confidence_decay"]
    assert len(decay1) == 1
    assert len(decay2) == 1
    assert decay1[0].id == decay2[0].id


# ---------------------------------------------------------------------------
# list_active_temporal_events filtering
# ---------------------------------------------------------------------------

def test_list_active_temporal_events_filters_by_type(conn):
    now = datetime.now(UTC)
    # Stage a drift event
    attr_id = _insert_attribute(conn, domain="personality", label="openness", value="high")
    _insert_history(conn, attr_id, (now - timedelta(days=10)).isoformat())
    _insert_history(conn, attr_id, (now - timedelta(days=5)).isoformat())
    # Stage a shift_cluster event
    for label in ("a", "b", "c"):
        sid = _insert_attribute(conn, domain="values", label=label, value="v")
        _insert_history(conn, sid, (now - timedelta(days=3)).isoformat())

    refresh_temporal_intelligence(conn)

    drift_only = list_active_temporal_events(conn, event_type="drift")
    assert all(e.event_type == "drift" for e in drift_only)

    shift_only = list_active_temporal_events(conn, event_type="shift_cluster")
    assert all(e.event_type == "shift_cluster" for e in shift_only)


def test_list_active_temporal_events_filters_by_domain(conn):
    now = datetime.now(UTC)
    attr_id = _insert_attribute(conn, domain="personality", label="openness", value="high")
    _insert_history(conn, attr_id, (now - timedelta(days=10)).isoformat())
    _insert_history(conn, attr_id, (now - timedelta(days=5)).isoformat())

    refresh_temporal_intelligence(conn)

    personality_events = list_active_temporal_events(conn, domain="personality")
    assert all(e.domain == "personality" for e in personality_events)

    goals_events = list_active_temporal_events(conn, domain="goals")
    assert not goals_events
