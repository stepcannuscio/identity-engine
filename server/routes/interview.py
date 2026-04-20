"""Guided interview preview/save API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from engine.interview_capture import preview_interview_answer, save_preview_attributes
from engine.privacy_broker import AuditedExternalExtractionConsentRequiredError
from engine.setup_state import resolve_active_provider_config
from server.db import get_db_connection
from server.models.schemas import (
    AttributeResponse,
    CapturePreviewWriteItem,
    CapturePreviewItem,
    InterviewPreviewRequest,
    InterviewPreviewResponse,
    InterviewResponse,
)
from server.routes.attributes import _serialize_attribute

router = APIRouter(tags=["interview"])


def _external_extraction_consent_response() -> JSONResponse:
    message = "Raw user input requires explicit consent before external extraction."
    return JSONResponse(
        {
            "error": "external_extraction_consent_required",
            "detail": message,
            "message": message,
        },
        status_code=409,
    )


def _accepted_to_dicts(items: list[CapturePreviewWriteItem] | list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
        else:
            normalized.append(item.model_dump())
    return normalized


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


@router.post("/interview/preview", response_model=InterviewPreviewResponse)
def preview(
    payload: InterviewPreviewRequest, request: Request
) -> InterviewPreviewResponse | JSONResponse:
    """Extract interview attributes without writing them to the database."""
    try:
        proposed: list[CapturePreviewItem] = []
        with get_db_connection() as conn:
            provider_config = resolve_active_provider_config(conn, request.app.state.llm_config)
            if payload.allow_external_extraction:
                extracted = preview_interview_answer(
                    payload.question,
                    payload.answer,
                    payload.domain,
                    provider_config,
                    allow_external_extraction=True,
                )
            else:
                extracted = preview_interview_answer(
                    payload.question,
                    payload.answer,
                    payload.domain,
                    provider_config,
                )
            for item in extracted:
                conflict = _find_conflict(conn, item["domain"], item["label"])
                proposed.append(
                    CapturePreviewItem(
                        domain=item["domain"],
                        label=item["label"],
                        value=item["value"],
                        elaboration=item.get("elaboration"),
                        mutability=item.get("mutability", "stable"),
                        confidence=float(item.get("confidence", 0.8)),
                        conflicts_with=_serialize_attribute(conflict) if conflict is not None else None,
                    )
                )
    except AuditedExternalExtractionConsentRequiredError:
        return _external_extraction_consent_response()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return InterviewPreviewResponse(proposed=proposed)


@router.post("/interview", response_model=InterviewResponse)
def interview(payload: InterviewPreviewRequest, request: Request) -> InterviewResponse | JSONResponse:
    """Persist accepted interview attributes."""
    try:
        with get_db_connection() as conn:
            accepted = payload.accepted
            if accepted is None:
                provider_config = resolve_active_provider_config(conn, request.app.state.llm_config)
                if payload.allow_external_extraction:
                    accepted = preview_interview_answer(
                        payload.question,
                        payload.answer,
                        payload.domain,
                        provider_config,
                        allow_external_extraction=True,
                    )
                else:
                    accepted = preview_interview_answer(
                        payload.question,
                        payload.answer,
                        payload.domain,
                        provider_config,
                    )
    except AuditedExternalExtractionConsentRequiredError:
        return _external_extraction_consent_response()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with get_db_connection() as conn:
        saved = save_preview_attributes(
            conn,
            _accepted_to_dicts(accepted),
        )

    return InterviewResponse(
        attributes_saved=len(saved),
        attributes=[
            AttributeResponse(
                id=str(item["id"]),
                domain=str(item["domain"]),
                label=str(item["label"]),
                value=str(item["value"]),
                elaboration=item.get("elaboration"),
                mutability=str(item["mutability"]),
                source=str(item["source"]),
                confidence=float(item["confidence"]),
                routing=str(item["routing"]),
                status=str(item["status"]),
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                last_confirmed=item["last_confirmed"],
            )
            for item in saved
        ],
    )
