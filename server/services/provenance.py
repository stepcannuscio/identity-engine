"""Privacy-safe provenance read helpers."""

from __future__ import annotations

from typing import cast

from config.settings import INFERRED
from db.inference_evidence import get_inference_evidence_for_attribute
from server.services.evidence import build_evidence_list_response
from server.models.schemas import (
    AttributeProvenanceResponse,
    AttributeResponse,
    ProvenanceEvidenceSummary,
)


def build_attribute_provenance_response(
    conn,
    attribute: AttributeResponse,
) -> AttributeProvenanceResponse:
    """Build a privacy-safe provenance payload for one attribute."""
    evidence: list[ProvenanceEvidenceSummary] = []
    if attribute.source == INFERRED:
        generalized = build_evidence_list_response(
            conn,
            target_type="attribute",
            target_id=attribute.id,
            kind="inference_evidence",
        )
        if generalized.evidence:
            evidence = [
                ProvenanceEvidenceSummary(
                    source_type=item.source_type,
                    summary=item.summary,
                    weight=(
                        float(cast(float | int | str, item.metadata["weight"]))
                        if item.metadata and item.metadata.get("weight") is not None
                        else None
                    ),
                )
                for item in generalized.evidence
            ]
        else:
            evidence = [
                ProvenanceEvidenceSummary(
                    source_type=record.source_type,
                    summary=(
                        f"Derived from {record.source_type.replace('_', ' ')}; "
                        "supporting detail retained locally."
                    ),
                    weight=record.weight,
                )
                for record in get_inference_evidence_for_attribute(conn, attribute.id)
            ]

    return AttributeProvenanceResponse(
        attribute_id=attribute.id,
        label=attribute.label,
        source=attribute.source,
        evidence=evidence,
    )
