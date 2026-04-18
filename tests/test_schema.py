"""Tests for the database schema using an in-memory plain SQLite connection.

SQLCipher is not required for schema logic tests. We use get_plain_connection()
so the CI environment does not need the SQLCipher C library.
"""

import sqlite3
import uuid

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains, INITIAL_DOMAINS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """Fresh in-memory SQLite connection with schema applied."""
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        yield c


def _domain_id(conn) -> str:
    """Return the id of the first domain, inserting one if needed."""
    row = conn.execute("SELECT id FROM domains LIMIT 1").fetchone()
    if row:
        return row[0]
    did = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO domains (id, name, description) VALUES (?, ?, ?)",
        (did, "test-domain", "A domain for testing"),
    )
    conn.commit()
    return did


def _insert_attribute(conn, **overrides) -> str:
    """Insert a minimal valid attribute and return its id."""
    did = _domain_id(conn)
    aid = str(uuid.uuid4())
    defaults = dict(
        id=aid,
        domain_id=did,
        label="test-label",
        value="test-value",
        mutability="stable",
        source="explicit",
        confidence=0.9,
        routing="local_only",
        status="active",
    )
    defaults.update(overrides)
    conn.execute(
        """INSERT INTO attributes
               (id, domain_id, label, value, mutability, source, confidence, routing, status)
           VALUES
               (:id, :domain_id, :label, :value, :mutability, :source,
                :confidence, :routing, :status)
        """,
        defaults,
    )
    conn.commit()
    return str(defaults["id"])


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------

def test_all_tables_created(conn):
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    expected = {
        "domains",
        "attributes",
        "attribute_history",
        "inference_evidence",
        "reflection_sessions",
        "preference_signals",
        "query_feedback",
    }
    assert expected.issubset(tables)


# ---------------------------------------------------------------------------
# CHECK constraints on attributes
# ---------------------------------------------------------------------------

def test_routing_rejects_invalid(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attribute(conn, routing="public", id=str(uuid.uuid4()), label="r-bad")


def test_routing_accepts_local_only(conn):
    aid = _insert_attribute(conn, routing="local_only", id=str(uuid.uuid4()), label="r-local")
    row = conn.execute("SELECT routing FROM attributes WHERE id = ?", (aid,)).fetchone()
    assert row[0] == "local_only"


def test_routing_accepts_external_ok(conn):
    aid = _insert_attribute(
        conn, routing="external_ok", id=str(uuid.uuid4()), label="r-ext", status="active"
    )
    row = conn.execute("SELECT routing FROM attributes WHERE id = ?", (aid,)).fetchone()
    assert row[0] == "external_ok"


def test_confidence_rejects_above_1(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attribute(conn, confidence=1.1, id=str(uuid.uuid4()), label="c-high")


def test_confidence_rejects_below_0(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attribute(conn, confidence=-0.1, id=str(uuid.uuid4()), label="c-low")


def test_confidence_accepts_boundary_values(conn):
    a1 = _insert_attribute(
        conn, confidence=0.0, id=str(uuid.uuid4()), label="c-zero", status="active"
    )
    a2 = _insert_attribute(
        conn, confidence=1.0, id=str(uuid.uuid4()), label="c-one", status="active"
    )
    assert conn.execute(
        "SELECT confidence FROM attributes WHERE id = ?", (a1,)
    ).fetchone()[0] == 0.0
    assert conn.execute(
        "SELECT confidence FROM attributes WHERE id = ?", (a2,)
    ).fetchone()[0] == 1.0


def test_status_rejects_invalid(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attribute(conn, status="pending", id=str(uuid.uuid4()), label="s-bad")


def test_status_accepts_valid_values(conn):
    for status, label in [
        ("active", "s-active"),
        ("confirmed", "s-confirmed"),
        ("superseded", "s-sup"),
        ("rejected", "s-rejected"),
        ("retracted", "s-ret"),
    ]:
        aid = _insert_attribute(conn, status=status, id=str(uuid.uuid4()), label=label)
        row = conn.execute("SELECT status FROM attributes WHERE id = ?", (aid,)).fetchone()
        assert row[0] == status


# ---------------------------------------------------------------------------
# Foreign key enforcement
# ---------------------------------------------------------------------------

def test_attribute_history_fk(conn):
    aid = _insert_attribute(conn)
    hid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO attribute_history (id, attribute_id, previous_value, changed_by)
           VALUES (?, ?, ?, ?)""",
        (hid, aid, "old-value", "user"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT attribute_id FROM attribute_history WHERE id = ?", (hid,)
    ).fetchone()
    assert row[0] == aid


def test_attribute_history_fk_rejects_missing_attribute(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO attribute_history (id, attribute_id, previous_value, changed_by)
               VALUES (?, ?, ?, ?)""",
            (str(uuid.uuid4()), "nonexistent-id", "val", "user"),
        )
        conn.commit()


def test_preference_signal_rejects_invalid_signal(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO preference_signals (
                id, category, subject, signal, strength, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "writing_style",
                "concise_responses",
                "maybe",
                3,
                "explicit_feedback",
            ),
        )
        conn.commit()


def test_preference_signal_rejects_invalid_strength(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO preference_signals (
                id, category, subject, signal, strength, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "writing_style",
                "concise_responses",
                "prefer",
                7,
                "explicit_feedback",
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_create_tables_is_idempotent(conn):
    """Running create_tables a second time must not raise."""
    create_tables(conn)  # already called in fixture; call again
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "attributes" in tables


def test_seed_domains_is_idempotent(conn):
    first = seed_domains(conn)
    second = seed_domains(conn)
    assert len(first) == len(INITIAL_DOMAINS)
    assert second == []  # nothing new on second run
    count = conn.execute("SELECT count(*) FROM domains").fetchone()[0]
    assert count == len(INITIAL_DOMAINS)
