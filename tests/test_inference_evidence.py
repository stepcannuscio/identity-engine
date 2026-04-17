"""Tests for the inference-evidence helper layer."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.inference_evidence import (
    InferenceEvidenceInput,
    get_inference_evidence_for_attribute,
    record_inference_evidence,
    record_inference_evidence_batch,
)
from db.schema import create_tables, seed_domains


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


def _domain_id(conn, name: str = "patterns") -> str:
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (name,)).fetchone()
    assert row is not None
    return str(row[0])


def _insert_attribute(
    conn,
    *,
    label: str,
    source: str,
    domain: str = "patterns",
) -> str:
    attribute_id = str(uuid.uuid4())
    now = "2026-04-16T12:00:00+00:00"
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, 'evolving', ?, 0.7, 'local_only', 'active', ?, ?, ?)
        """,
        (
            attribute_id,
            _domain_id(conn, domain),
            label,
            "Test value.",
            None,
            source,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return attribute_id


def test_record_inference_evidence_inserts_single_row(conn):
    attribute_id = _insert_attribute(conn, label="work_style", source="inferred")

    record = record_inference_evidence(
        conn,
        attribute_id,
        InferenceEvidenceInput(
            source_type="reflection_session",
            source_ref="session-123",
            supporting_text="I do my best thinking in uninterrupted blocks.",
            weight=0.8,
        ),
    )

    row = conn.execute(
        """
        SELECT source_type, source_ref, supporting_text, weight
        FROM inference_evidence
        WHERE attribute_id = ?
        """,
        (attribute_id,),
    ).fetchone()

    assert row == (
        "reflection_session",
        "session-123",
        "I do my best thinking in uninterrupted blocks.",
        pytest.approx(0.8),
    )
    assert record.attribute_id == attribute_id
    assert record.source_type == "reflection_session"


def test_record_inference_evidence_batch_inserts_multiple_rows(conn):
    attribute_id = _insert_attribute(conn, label="meeting_load", source="inferred")

    records = record_inference_evidence_batch(
        conn,
        attribute_id,
        [
            InferenceEvidenceInput(
                source_type="capture",
                source_ref="capture-1",
                supporting_text="Back-to-back meetings drain me.",
                weight=0.6,
            ),
            InferenceEvidenceInput(
                source_type="reflection_session",
                source_ref="session-7",
                supporting_text="I need recovery time after meeting-heavy days.",
                weight=0.9,
            ),
        ],
    )

    count = conn.execute(
        "SELECT count(*) FROM inference_evidence WHERE attribute_id = ?",
        (attribute_id,),
    ).fetchone()[0]

    assert count == 2
    assert [record.source_type for record in records] == ["capture", "reflection_session"]


def test_record_inference_evidence_rejects_invalid_weight_without_echoing_text(conn, caplog):
    attribute_id = _insert_attribute(conn, label="focus_window", source="inferred")
    evidence_text = "Private supporting excerpt for test coverage."

    with pytest.raises(ValueError, match="weight must be between 0.0 and 1.0") as excinfo:
        record_inference_evidence(
            conn,
            attribute_id,
            InferenceEvidenceInput(
                source_type="capture",
                supporting_text=evidence_text,
                weight=1.2,
            ),
        )

    assert evidence_text not in str(excinfo.value)
    assert evidence_text not in caplog.text


def test_record_inference_evidence_rejects_missing_attribute(conn):
    with pytest.raises(ValueError, match="missing attribute"):
        record_inference_evidence(
            conn,
            "missing-attribute-id",
            InferenceEvidenceInput(
                source_type="capture",
                supporting_text="Should never be written.",
                weight=0.5,
            ),
        )


def test_record_inference_evidence_rejects_non_inferred_attributes(conn):
    attribute_id = _insert_attribute(conn, label="stated_goal", source="explicit", domain="goals")

    with pytest.raises(ValueError, match="only be recorded for inferred attributes"):
        record_inference_evidence(
            conn,
            attribute_id,
            InferenceEvidenceInput(
                source_type="capture",
                supporting_text="This was stated directly.",
                weight=0.5,
            ),
        )


def test_get_inference_evidence_for_attribute_returns_predictable_order(conn):
    attribute_id = _insert_attribute(conn, label="energy_pattern", source="inferred")
    record_inference_evidence_batch(
        conn,
        attribute_id,
        [
            InferenceEvidenceInput(
                source_type="capture",
                source_ref="cap-1",
                supporting_text="I focus early in the day.",
                weight=0.4,
            ),
            InferenceEvidenceInput(
                source_type="reflection_session",
                source_ref="session-2",
                supporting_text="I avoid late calls because my thinking is slower then.",
                weight=0.7,
            ),
        ],
    )

    records = get_inference_evidence_for_attribute(conn, attribute_id)

    assert [record.source_type for record in records] == ["capture", "reflection_session"]
    assert records[0].supporting_text == "I focus early in the day."
    assert records[1].supporting_text == "I avoid late calls because my thinking is slower then."


def test_get_inference_evidence_for_attribute_returns_empty_list_when_absent(conn):
    attribute_id = _insert_attribute(conn, label="untested_inference", source="inferred")

    assert get_inference_evidence_for_attribute(conn, attribute_id) == []
