"""Quick-capture API routes for previewing and saving extracted attributes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from engine.capture import capture as save_capture
from engine.capture import preview_capture
from engine.capture import save_preview_attributes
from engine.setup_state import resolve_active_provider_config
from server.db import get_db_connection
from server.models.schemas import (
    CapturePreviewItem,
    CapturePreviewResponse,
    CaptureRequest,
    CaptureResponse,
)
from server.routes.attributes import _serialize_attribute

router = APIRouter(tags=["capture"])


def _find_conflict(conn, domain: str, label: str):
    return conn.execute(
        """
        SELECT
            a.id,
            d.name,
            a.label,
            a.value,
            a.elaboration,
            a.mutability,
            a.source,
            a.confidence,
            a.routing,
            a.status,
            a.created_at,
            a.updated_at,
            a.last_confirmed
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE d.name = ? AND a.label = ? AND a.status IN ('active', 'confirmed')
        """,
        (domain, label),
    ).fetchone()


@router.post("/capture/preview", response_model=CapturePreviewResponse)
def preview(payload: CaptureRequest, request: Request) -> CapturePreviewResponse:
    """Extract capture attributes without writing them to the database."""
    proposed: list[CapturePreviewItem] = []
    with get_db_connection() as conn:
        extracted = preview_capture(
            payload.text,
            payload.domain_hint,
            resolve_active_provider_config(conn, request.app.state.llm_config),
        )
        for item in extracted:
            conflict = _find_conflict(conn, item["domain"], item["label"])
            proposed.append(
                CapturePreviewItem(
                    domain=item["domain"],
                    label=item["label"],
                    value=item["value"],
                    elaboration=item["elaboration"],
                    mutability=item["mutability"],
                    confidence=float(item["confidence"]),
                    conflicts_with=_serialize_attribute(conflict) if conflict is not None else None,
                )
            )
    return CapturePreviewResponse(proposed=proposed)


@router.post("/capture", response_model=CaptureResponse)
def capture(payload: CaptureRequest, request: Request) -> CaptureResponse:
    """Persist quick-capture attributes in non-interactive mode."""
    with get_db_connection() as conn:
        if payload.accepted is not None:
            saved = save_preview_attributes(
                conn,
                [item.model_dump() for item in payload.accepted],
            )
        else:
            saved = save_capture(
                payload.text,
                payload.domain_hint,
                conn,
                resolve_active_provider_config(conn, request.app.state.llm_config),
                confirm=False,
            )
        attributes = []
        for item in saved:
            row = _find_conflict(conn, item["domain"], item["label"])
            if row is not None:
                attributes.append(_serialize_attribute(row))
    return CaptureResponse(attributes_saved=len(attributes), attributes=attributes)
