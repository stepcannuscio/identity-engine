"""Privacy-safe provenance read helpers."""

from __future__ import annotations

import re

from config.settings import INFERRED
from db.inference_evidence import InferenceEvidenceRecord, get_inference_evidence_for_attribute
from server.models.schemas import (
    AttributeProvenanceResponse,
    AttributeResponse,
    ProvenanceEvidenceSummary,
)

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_SOURCE_LABELS = {
    "capture": "captured note",
    "journal": "journal entry",
    "reflection_session": "reflection session",
}


def _source_label(source_type: str) -> str:
    normalized = source_type.strip().lower()
    if normalized in _SOURCE_LABELS:
        return _SOURCE_LABELS[normalized]
    return normalized.replace("_", " ") or "local evidence"


def _word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def _summarize_evidence(record: InferenceEvidenceRecord) -> ProvenanceEvidenceSummary:
    label = _source_label(record.source_type)
    word_count = _word_count(record.supporting_text)

    if word_count > 0:
        summary = f"Derived from {label}; {word_count}-word supporting note kept local."
    elif record.source_ref:
        summary = f"Derived from {label}; linked local reference retained."
    else:
        summary = f"Derived from {label}; supporting detail retained locally."

    return ProvenanceEvidenceSummary(
        source_type=record.source_type,
        summary=summary,
        weight=record.weight,
    )


def build_attribute_provenance_response(
    conn,
    attribute: AttributeResponse,
) -> AttributeProvenanceResponse:
    """Build a privacy-safe provenance payload for one attribute."""
    evidence: list[ProvenanceEvidenceSummary] = []
    if attribute.source == INFERRED:
        evidence = [
            _summarize_evidence(record)
            for record in get_inference_evidence_for_attribute(conn, attribute.id)
        ]

    return AttributeProvenanceResponse(
        attribute_id=attribute.id,
        label=attribute.label,
        source=attribute.source,
        evidence=evidence,
    )
