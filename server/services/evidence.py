"""Privacy-safe generalized evidence read helpers."""

from __future__ import annotations

from typing import Any, cast

from db.evidence import list_evidence_for_target
from server.models.schemas import EvidenceListResponse, EvidenceSummaryResponse


def build_evidence_list_response(
    conn,
    *,
    target_type: str,
    target_id: str,
    kind: str | None = None,
) -> EvidenceListResponse:
    """Build one generalized evidence response for a target."""
    evidence = list_evidence_for_target(
        conn,
        target_type=target_type,
        target_id=target_id,
        kind=kind,
    )
    return EvidenceListResponse(
        target_type=cast(
            Any,
            target_type,
        ),
        target_id=target_id,
        evidence=[
            EvidenceSummaryResponse(
                kind=item.kind,
                source_type=item.source_type,
                routing=cast(Any, item.routing),
                summary=item.summary,
                source_ref=item.source_ref,
                metadata=item.metadata,
                created_at=cast(Any, item.created_at),
            )
            for item in evidence
        ],
    )
