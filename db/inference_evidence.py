"""Helpers for storing and loading inference evidence records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import uuid

from config.settings import INFERRED


@dataclass(frozen=True, slots=True)
class InferenceEvidenceInput:
    """Typed input for one inference-evidence row."""

    source_type: str
    source_ref: str | None = None
    supporting_text: str | None = field(default=None, repr=False)
    weight: float | None = None


@dataclass(frozen=True, slots=True)
class InferenceEvidenceRecord:
    """Stored inference-evidence row."""

    id: str
    attribute_id: str
    source_type: str
    source_ref: str | None
    supporting_text: str | None = field(repr=False)
    weight: float | None
    created_at: str


def _validate_target_attribute(conn, attribute_id: str) -> None:
    row = conn.execute(
        "SELECT source FROM attributes WHERE id = ?",
        (attribute_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Cannot record inference evidence for a missing attribute.")
    if str(row[0]) != INFERRED:
        raise ValueError("Inference evidence can only be recorded for inferred attributes.")


def _normalize_evidence_item(item: InferenceEvidenceInput) -> InferenceEvidenceInput:
    source_type = item.source_type.strip()
    if not source_type:
        raise ValueError("Inference evidence source_type is required.")

    weight = item.weight
    if weight is not None:
        weight = float(weight)
        if not 0.0 <= weight <= 1.0:
            raise ValueError("Inference evidence weight must be between 0.0 and 1.0.")

    return InferenceEvidenceInput(
        source_type=source_type,
        source_ref=item.source_ref,
        supporting_text=item.supporting_text,
        weight=weight,
    )


def record_inference_evidence(
    conn,
    attribute_id: str,
    evidence_item: InferenceEvidenceInput,
) -> InferenceEvidenceRecord:
    """Insert one evidence row for an inferred attribute."""
    return record_inference_evidence_batch(conn, attribute_id, [evidence_item])[0]


def record_inference_evidence_batch(
    conn,
    attribute_id: str,
    evidence_items: list[InferenceEvidenceInput],
) -> list[InferenceEvidenceRecord]:
    """Insert one or more evidence rows for an inferred attribute."""
    if not evidence_items:
        return []

    _validate_target_attribute(conn, attribute_id)
    normalized_items = [_normalize_evidence_item(item) for item in evidence_items]

    base_time = datetime.now(UTC)
    records: list[InferenceEvidenceRecord] = []
    for index, item in enumerate(normalized_items):
        created_at = (base_time + timedelta(microseconds=index)).isoformat()
        records.append(
            InferenceEvidenceRecord(
                id=str(uuid.uuid4()),
                attribute_id=attribute_id,
                source_type=item.source_type,
                source_ref=item.source_ref,
                supporting_text=item.supporting_text,
                weight=item.weight,
                created_at=created_at,
            )
        )

    conn.executemany(
        """
        INSERT INTO inference_evidence (
            id,
            attribute_id,
            source_type,
            source_ref,
            supporting_text,
            weight,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                record.id,
                record.attribute_id,
                record.source_type,
                record.source_ref,
                record.supporting_text,
                record.weight,
                record.created_at,
            )
            for record in records
        ],
    )
    conn.commit()
    return records


def get_inference_evidence_for_attribute(
    conn,
    attribute_id: str,
) -> list[InferenceEvidenceRecord]:
    """Return inference-evidence rows for one attribute in creation order."""
    rows = conn.execute(
        """
        SELECT
            id,
            attribute_id,
            source_type,
            source_ref,
            supporting_text,
            weight,
            created_at
        FROM inference_evidence
        WHERE attribute_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (attribute_id,),
    ).fetchall()

    return [
        InferenceEvidenceRecord(
            id=str(row[0]),
            attribute_id=str(row[1]),
            source_type=str(row[2]),
            source_ref=row[3],
            supporting_text=row[4],
            weight=None if row[5] is None else float(row[5]),
            created_at=str(row[6]),
        )
        for row in rows
    ]
