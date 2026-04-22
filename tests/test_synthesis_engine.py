"""Tests for deterministic cross-domain synthesis helpers."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.contradiction_detector import refresh_contradiction_flags
from engine.synthesis_engine import refresh_cross_domain_synthesis


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as connection:
        create_tables(connection)
        seed_domains(connection)
        yield connection


def _insert_attribute(
    conn,
    *,
    domain: str,
    label: str,
    value: str,
    confidence: float = 0.84,
    status: str = "confirmed",
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
        VALUES (?, ?, ?, ?, ?, 'stable', 'explicit', ?, 'local_only', ?, ?, ?, ?)
        """,
        (
            attribute_id,
            domain_id,
            label,
            value,
            None,
            confidence,
            status,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return attribute_id


def test_refresh_cross_domain_synthesis_stages_multi_domain_theme(conn):
    _insert_attribute(
        conn,
        domain="personality",
        label="social_orientation",
        value="I am introverted and need quiet to recharge after groups.",
    )
    _insert_attribute(
        conn,
        domain="patterns",
        label="meeting_energy",
        value="Large meetings drain my social battery quickly.",
    )
    _insert_attribute(
        conn,
        domain="relationships",
        label="connection_needs",
        value="I prefer close one-on-one conversations over crowded gatherings.",
    )

    syntheses = refresh_cross_domain_synthesis(conn)

    assert len(syntheses) == 1
    synthesis = syntheses[0]
    assert synthesis.theme_label == "social energy"
    assert synthesis.domains_involved == ["patterns", "personality", "relationships"]
    assert len(synthesis.evidence_ids) == 3
    assert synthesis.strength >= 0.7
    assert "social energy" in (synthesis.synthesis_text or "")


def test_refresh_cross_domain_synthesis_is_idempotent_for_same_evidence(conn):
    for domain, label, value in [
        ("personality", "social_orientation", "I am introverted and need quiet to recharge."),
        ("patterns", "meeting_energy", "Long group meetings drain my social battery."),
        ("relationships", "connection_needs", "I trust people most in small one-on-one settings."),
    ]:
        _insert_attribute(conn, domain=domain, label=label, value=value)

    first = refresh_cross_domain_synthesis(conn)
    second = refresh_cross_domain_synthesis(conn)
    stored = conn.execute("SELECT COUNT(*) FROM cross_domain_synthesis").fetchone()[0]

    assert len(first) == 1
    assert len(second) == 1
    assert stored == 1


def test_refresh_cross_domain_synthesis_skips_two_domain_clusters(conn):
    _insert_attribute(
        conn,
        domain="personality",
        label="social_orientation",
        value="I am introverted and need quiet to recharge.",
    )
    _insert_attribute(
        conn,
        domain="patterns",
        label="meeting_energy",
        value="Group meetings drain my social battery quickly.",
    )

    syntheses = refresh_cross_domain_synthesis(conn)

    assert syntheses == []
    stored = conn.execute("SELECT COUNT(*) FROM cross_domain_synthesis").fetchone()[0]
    assert stored == 0


def test_refresh_contradiction_flags_stages_opposed_high_confidence_attributes(conn):
    _insert_attribute(
        conn,
        domain="patterns",
        label="workflow_structure",
        value="I do my best work with highly structured routines and organized plans.",
    )
    _insert_attribute(
        conn,
        domain="goals",
        label="exploration_style",
        value="I stay creative when I keep things spontaneous and flexible.",
    )

    flags = refresh_contradiction_flags(conn)

    assert len(flags) == 1
    flag = flags[0]
    assert flag.polarity_axis == "structure_spontaneity"
    assert {flag.attribute_a_domain, flag.attribute_b_domain} == {"patterns", "goals"}
    assert flag.confidence >= 0.8


def test_refresh_contradiction_flags_skips_low_confidence_pairs(conn):
    _insert_attribute(
        conn,
        domain="patterns",
        label="workflow_structure",
        value="I prefer highly structured routines and organized plans.",
        confidence=0.62,
    )
    _insert_attribute(
        conn,
        domain="goals",
        label="exploration_style",
        value="I stay creative when I keep things spontaneous and flexible.",
        confidence=0.61,
    )

    flags = refresh_contradiction_flags(conn)

    assert flags == []
    stored = conn.execute("SELECT COUNT(*) FROM contradiction_flags").fetchone()[0]
    assert stored == 0
