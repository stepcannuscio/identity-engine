"""Generalized evidence read routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from server.db import get_db_connection
from server.models.schemas import EvidenceListResponse
from server.services.evidence import build_evidence_list_response

router = APIRouter(tags=["evidence"])
_TARGET_TYPES = {"attribute", "artifact", "session", "query_feedback", "voice_feedback"}


@router.get("/evidence", response_model=EvidenceListResponse)
def get_evidence(
    request: Request,
    target_type: str,
    target_id: str,
    kind: str | None = None,
) -> EvidenceListResponse:
    """Return privacy-safe generalized evidence for one target."""
    _ = request
    normalized_target_type = target_type.strip().lower()
    if normalized_target_type not in _TARGET_TYPES:
        raise HTTPException(status_code=422, detail="unsupported target_type")
    normalized_target_id = target_id.strip()
    if not normalized_target_id:
        raise HTTPException(status_code=422, detail="target_id is required")
    normalized_kind = (kind or "").strip() or None

    with get_db_connection() as conn:
        return build_evidence_list_response(
            conn,
            target_type=normalized_target_type,
            target_id=normalized_target_id,
            kind=normalized_kind,
        )
