"""Tests for scripts/view_db.py using an in-memory plain SQLite connection.

SQLCipher is not required. We use get_plain_connection() and call view(conn)
directly so the tests never touch the keychain or the real database file.
"""

import uuid
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains

# view() lives in scripts/, which is not a package — import by path manipulation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from view_db import view


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory DB with schema and the 8 default domains seeded."""
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


def _domain_id(conn, name="personality"):
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (name,)).fetchone()
    assert row, f"Domain '{name}' not found — was seed_domains() called?"
    return row[0]


def _insert_attr(conn, domain_name="personality", **overrides):
    """Insert a minimal active attribute and return its id."""
    did = _domain_id(conn, domain_name)
    aid = str(uuid.uuid4())
    defaults = dict(
        id=aid,
        domain_id=did,
        label="test_label",
        value="Test value.",
        elaboration=None,
        mutability="stable",
        source="explicit",
        confidence=0.9,
        routing="local_only",
        status="active",
        updated_at="2026-01-01 12:00:00",
    )
    defaults.update(overrides)
    conn.execute(
        """INSERT INTO attributes
               (id, domain_id, label, value, elaboration,
                mutability, source, confidence, routing, status, updated_at)
           VALUES
               (:id, :domain_id, :label, :value, :elaboration,
                :mutability, :source, :confidence, :routing, :status, :updated_at)
        """,
        defaults,
    )
    conn.commit()
    return defaults["id"]


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------

def test_empty_store_header(conn, capsys):
    view(conn)
    out = capsys.readouterr().out
    assert "IDENTITY STORE" in out
    assert "no attributes stored yet" in out


def test_empty_store_shows_all_domains(conn, capsys):
    view(conn)
    out = capsys.readouterr().out
    for domain in ("PERSONALITY", "VALUES", "GOALS", "PATTERNS",
                   "VOICE", "RELATIONSHIPS", "FEARS", "BELIEFS"):
        assert domain in out


def test_empty_store_placeholder(conn, capsys):
    view(conn)
    out = capsys.readouterr().out
    assert "(no active attributes)" in out


def test_empty_store_no_last_updated(conn, capsys):
    view(conn)
    out = capsys.readouterr().out
    assert "Last updated" not in out


# ---------------------------------------------------------------------------
# Single attribute
# ---------------------------------------------------------------------------

def test_single_attr_header_count(conn, capsys):
    _insert_attr(conn)
    view(conn)
    out = capsys.readouterr().out
    assert "1 attribute across 1 domain" in out


def test_single_attr_label_in_output(conn, capsys):
    _insert_attr(conn, label="recharge_style")
    view(conn)
    out = capsys.readouterr().out
    assert "recharge_style" in out


def test_single_attr_value_in_output(conn, capsys):
    _insert_attr(conn, value="Introvert — quiet time after social events.")
    view(conn)
    out = capsys.readouterr().out
    assert "Introvert — quiet time after social events." in out


def test_single_attr_badge_format(conn, capsys):
    _insert_attr(conn, mutability="evolving", source="inferred", confidence=0.75)
    view(conn)
    out = capsys.readouterr().out
    assert "[evolving, inferred, 0.75]" in out


def test_single_attr_routing_shown(conn, capsys):
    _insert_attr(conn, routing="external_ok", label="ext_attr")
    view(conn)
    out = capsys.readouterr().out
    assert "external_ok" in out


def test_single_attr_elaboration_shown(conn, capsys):
    _insert_attr(conn, elaboration="Most pronounced after large group events.")
    view(conn)
    out = capsys.readouterr().out
    assert "Most pronounced after large group events." in out


def test_single_attr_no_elaboration_no_blank_line(conn, capsys):
    _insert_attr(conn, elaboration=None)
    view(conn)
    out = capsys.readouterr().out
    # Should not contain a line that is just whitespace between the label line and
    # the domain header of the next section — rough check: no double blank lines.
    assert "\n\n\n" not in out


def test_single_attr_last_updated_shown(conn, capsys):
    _insert_attr(conn, updated_at="2026-03-15 09:30:00")
    view(conn)
    out = capsys.readouterr().out
    assert "Last updated: 2026-03-15 09:30:00" in out


# ---------------------------------------------------------------------------
# Multiple attributes and domains
# ---------------------------------------------------------------------------

def test_multiple_attrs_count(conn, capsys):
    _insert_attr(conn, label="attr_one", domain_name="personality")
    _insert_attr(conn, label="attr_two", domain_name="personality")
    _insert_attr(conn, label="attr_three", domain_name="values")
    view(conn)
    out = capsys.readouterr().out
    assert "3 attributes across 2 domains" in out


def test_domain_header_shows_count(conn, capsys):
    _insert_attr(conn, label="a1", domain_name="personality")
    _insert_attr(conn, label="a2", domain_name="personality")
    view(conn)
    out = capsys.readouterr().out
    assert "PERSONALITY (2)" in out


def test_empty_domains_counted_in_footer(conn, capsys):
    _insert_attr(conn, domain_name="personality")
    view(conn)
    out = capsys.readouterr().out
    # 8 domains seeded; 1 has data → 7 empty
    assert "7 empty" in out


def test_footer_summary_parts(conn, capsys):
    _insert_attr(conn, label="a1", domain_name="personality")
    _insert_attr(conn, label="a2", domain_name="values")
    view(conn)
    out = capsys.readouterr().out
    assert "2 domains with data" in out
    assert "2 total attributes" in out


def test_last_updated_is_most_recent(conn, capsys):
    _insert_attr(conn, label="old_attr", updated_at="2026-01-01 00:00:00")
    _insert_attr(conn, label="new_attr", updated_at="2026-04-06 14:23:01")
    view(conn)
    out = capsys.readouterr().out
    assert "Last updated: 2026-04-06 14:23:01" in out


# ---------------------------------------------------------------------------
# Status filtering — only active attributes shown
# ---------------------------------------------------------------------------

def test_superseded_attr_excluded(conn, capsys):
    _insert_attr(conn, label="old_value", status="superseded")
    view(conn)
    out = capsys.readouterr().out
    assert "old_value" not in out
    assert "no attributes stored yet" in out


def test_retracted_attr_excluded(conn, capsys):
    _insert_attr(conn, label="retracted_value", status="retracted")
    view(conn)
    out = capsys.readouterr().out
    assert "retracted_value" not in out


def test_active_and_superseded_same_domain(conn, capsys):
    _insert_attr(conn, label="current", status="active")
    _insert_attr(conn, label="old", status="superseded", id=str(uuid.uuid4()))
    view(conn)
    out = capsys.readouterr().out
    assert "current" in out
    assert "old" not in out
    assert "PERSONALITY (1)" in out
