"""Artifact ingestion API routes."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, TypeGuard, cast

from fastapi import APIRouter, HTTPException, Request

from config.llm_router import ConfigurationError
from engine.artifact_analysis import analyze_artifact, get_artifact_analysis, promote_artifact_analysis
from engine.artifact_ingestion import get_artifact_record, ingest_artifact
from engine.local_document_parser import extract_docx_text, extract_pdf_text
from engine.setup_state import resolve_local_provider_config
from server.db import get_db_connection
from server.models.schemas import (
    ArtifactAnalysisAttributeCandidate,
    ArtifactAnalysisPreferenceCandidate,
    ArtifactAnalysisResponse,
    ArtifactIngestResponse,
    ArtifactPromoteRequest,
    ArtifactPromoteResponse,
)

router = APIRouter(tags=["artifacts"])

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ""}
_DOCX_SUFFIXES = {".docx"}
_PDF_SUFFIXES = {".pdf"}
MAX_ARTIFACT_REQUEST_BYTES = 5 * 1024 * 1024
MAX_ARTIFACT_FILE_BYTES = 5 * 1024 * 1024
MAX_ARTIFACT_TEXT_CHARS = 250_000


class _UploadLike(Protocol):
    filename: str | None
    file: BinaryIO


def _request_size_guard(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        size = int(content_length)
    except ValueError:
        return
    if size > MAX_ARTIFACT_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="artifact request exceeds size limit")


def _text_size_guard(text: str) -> str:
    if len(text) > MAX_ARTIFACT_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="artifact text exceeds size limit")
    return text


def _decode_upload(upload: _UploadLike) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in _TEXT_SUFFIXES | _DOCX_SUFFIXES | _PDF_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported artifact file type")

    raw = upload.file.read()
    if len(raw) > MAX_ARTIFACT_FILE_BYTES:
        raise HTTPException(status_code=413, detail="artifact file exceeds size limit")
    if suffix in _PDF_SUFFIXES:
        text = extract_pdf_text(raw)
        if not text.strip():
            raise HTTPException(status_code=400, detail="unable to extract text from pdf")
        return _text_size_guard(text)
    if suffix in _DOCX_SUFFIXES:
        try:
            text = extract_docx_text(raw)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="unable to extract text from docx") from exc
        if not text.strip():
            raise HTTPException(status_code=400, detail="unable to extract text from docx")
        return _text_size_guard(text)
    try:
        return _text_size_guard(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="artifact upload must be utf-8 text") from exc


def _is_upload_file(value: object) -> TypeGuard[_UploadLike]:
    return hasattr(value, "filename") and hasattr(value, "file")


def _parse_tags(raw_tags: object) -> list[str]:
    if raw_tags is None:
        return []
    if isinstance(raw_tags, list):
        return [str(tag).strip() for tag in raw_tags if str(tag).strip()]
    if isinstance(raw_tags, str):
        stripped = raw_tags.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in stripped.split(",")]
        if isinstance(parsed, list):
            return [str(tag).strip() for tag in parsed if str(tag).strip()]
    raise HTTPException(status_code=400, detail="tags must be a list or comma-separated string")


def _analysis_response(artifact_id: str, analysis: dict[str, object] | None) -> ArtifactAnalysisResponse:
    if not isinstance(analysis, dict):
        return ArtifactAnalysisResponse(artifact_id=artifact_id, analysis_status="not_analyzed")

    analysis_method_value = str(analysis.get("analysis_method", "")).strip()
    if analysis_method_value not in {"model", "heuristic_fallback"}:
        analysis_method_value = ""

    def _attribute_candidate(item: object) -> ArtifactAnalysisAttributeCandidate | None:
        if not isinstance(item, dict):
            return None
        mutability_value = str(item.get("mutability", "evolving"))
        if mutability_value not in {"stable", "evolving"}:
            mutability_value = "evolving"
        status_value = str(item.get("status", "pending"))
        if status_value not in {"pending", "promoted"}:
            status_value = "pending"
        return ArtifactAnalysisAttributeCandidate(
            candidate_id=str(item.get("candidate_id", "")),
            domain=str(item.get("domain", "")),
            label=str(item.get("label", "")),
            value=str(item.get("value", "")),
            elaboration=item.get("elaboration"),
            mutability=cast(Literal["stable", "evolving"], mutability_value),
            confidence=float(item.get("confidence", 0.0) or 0.0),
            status=cast(Literal["pending", "promoted"], status_value),
        )

    def _preference_candidate(item: object) -> ArtifactAnalysisPreferenceCandidate | None:
        if not isinstance(item, dict):
            return None
        signal_value = str(item.get("signal", "prefer"))
        if signal_value not in {"like", "dislike", "accept", "reject", "prefer", "avoid"}:
            signal_value = "prefer"
        status_value = str(item.get("status", "pending"))
        if status_value not in {"pending", "promoted"}:
            status_value = "pending"
        return ArtifactAnalysisPreferenceCandidate(
            candidate_id=str(item.get("candidate_id", "")),
            category=str(item.get("category", "")),
            subject=str(item.get("subject", "")),
            signal=cast(Literal["like", "dislike", "accept", "reject", "prefer", "avoid"], signal_value),
            strength=int(item.get("strength", 3) or 3),
            summary=str(item.get("summary", "")).strip() or None,
            status=cast(Literal["pending", "promoted"], status_value),
        )

    analyzed_at = analysis.get("analyzed_at")
    parsed_analyzed_at = None
    if isinstance(analyzed_at, str) and analyzed_at.strip():
        try:
            parsed_analyzed_at = datetime.fromisoformat(analyzed_at.replace("Z", "+00:00"))
        except ValueError:
            parsed_analyzed_at = None
    descriptor_tokens = analysis.get("descriptor_tokens")
    attribute_candidates = analysis.get("candidate_attributes")
    preference_candidates = analysis.get("candidate_preferences")
    return ArtifactAnalysisResponse(
        artifact_id=artifact_id,
        analysis_status="analyzed",
        analysis_method=cast(Literal["model", "heuristic_fallback"] | None, analysis_method_value or None),
        analysis_warning=str(analysis.get("analysis_warning", "")).strip() or None,
        content_kind=str(analysis.get("content_kind", "")).strip() or None,
        summary=str(analysis.get("summary", "")).strip() or None,
        descriptor_tokens=[
            str(token)
            for token in descriptor_tokens
            if str(token).strip()
        ] if isinstance(descriptor_tokens, list) else [],
        candidate_attributes=[
            candidate
            for candidate in (
                _attribute_candidate(item) for item in attribute_candidates
            )
            if candidate is not None
        ] if isinstance(attribute_candidates, list) else [],
        candidate_preferences=[
            candidate
            for candidate in (
                _preference_candidate(item) for item in preference_candidates
            )
            if candidate is not None
        ] if isinstance(preference_candidates, list) else [],
        analyzed_at=parsed_analyzed_at,
    )


@router.post("/artifacts", response_model=ArtifactIngestResponse)
async def create_artifact(request: Request) -> ArtifactIngestResponse:
    """Ingest a local artifact from JSON text or multipart upload."""
    _request_size_guard(request)
    content_type = request.headers.get("content-type", "")
    text: str | None = None
    title: str | None = None
    artifact_type: str | None = None
    source: str | None = None
    domain: str | None = None
    filename: str | None = None
    metadata: dict[str, object] | None = None
    tags: list[str] = []

    if "application/json" in content_type:
        raw_body = await request.body()
        if len(raw_body) > MAX_ARTIFACT_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="artifact request exceeds size limit")
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid json body") from exc
        text = payload.get("text")
        title = payload.get("title")
        artifact_type = payload.get("type")
        source = payload.get("source")
        domain = payload.get("domain")
        metadata = payload.get("metadata")
        tags = _parse_tags(payload.get("tags"))
    elif (
        "multipart/form-data" in content_type
        or "application/x-www-form-urlencoded" in content_type
    ):
        form = await request.form()
        upload = form.get("file")
        text = str(form.get("text") or "").strip() or None
        title = str(form.get("title") or "").strip() or None
        artifact_type = str(form.get("type") or "").strip() or None
        source = str(form.get("source") or "").strip() or None
        domain = str(form.get("domain") or "").strip() or None
        raw_metadata = form.get("metadata")
        tags = _parse_tags(form.get("tags"))
        if raw_metadata:
            try:
                metadata = json.loads(str(raw_metadata))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="metadata must be valid json") from exc
        if _is_upload_file(upload):
            filename = upload.filename
            if text is not None:
                raise HTTPException(status_code=400, detail="provide either text or file, not both")
            text = _decode_upload(upload)
    else:
        raise HTTPException(status_code=415, detail="unsupported content type")

    if text is None:
        raise HTTPException(status_code=400, detail="artifact text or file is required")
    text = _text_size_guard(text)

    with get_db_connection() as conn:
        try:
            result = ingest_artifact(
                conn,
                text=text,
                title=title,
                artifact_type=artifact_type or ("upload" if filename else "note"),
                source=source or ("upload" if filename else "capture"),
                domain=domain,
                filename=filename,
                metadata=metadata,
                tags=tags,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ArtifactIngestResponse(
        artifact_id=result.artifact_id,
        chunk_count=result.chunk_count,
        tags=sorted({tag.strip().lower() for tag in tags if tag.strip()}),
        analysis_status="not_analyzed",
    )


@router.post("/artifacts/{artifact_id}/analyze", response_model=ArtifactAnalysisResponse)
def analyze_uploaded_artifact(artifact_id: str, request: Request) -> ArtifactAnalysisResponse:
    """Analyze one artifact locally and return reviewable candidates."""
    with get_db_connection() as conn:
        if get_artifact_record(conn, artifact_id) is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        try:
            provider_config = resolve_local_provider_config(request.app.state.llm_config)
        except ConfigurationError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Artifact analysis requires a local model. Enable a local provider to analyze uploads."
                ),
            ) from exc
        try:
            result = analyze_artifact(conn, artifact_id, provider_config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _analysis_response(
        artifact_id,
        {
            "analyzed_at": result.analyzed_at,
            "analysis_method": getattr(result, "analysis_method", "model"),
            "analysis_warning": getattr(result, "analysis_warning", None),
            "content_kind": result.content_kind,
            "summary": result.summary,
            "descriptor_tokens": result.descriptor_tokens,
            "candidate_attributes": result.candidate_attributes,
            "candidate_preferences": result.candidate_preferences,
        },
    )


@router.post("/artifacts/{artifact_id}/promote", response_model=ArtifactPromoteResponse)
def promote_uploaded_artifact(
    artifact_id: str,
    payload: ArtifactPromoteRequest,
) -> ArtifactPromoteResponse:
    """Promote selected artifact-analysis candidates into canonical stores."""
    with get_db_connection() as conn:
        if get_artifact_record(conn, artifact_id) is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        try:
            result = promote_artifact_analysis(
                conn,
                artifact_id,
                selected_attributes=[item.model_dump(mode="json") for item in payload.selected_attributes],
                selected_preferences=[item.model_dump(mode="json") for item in payload.selected_preferences],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ArtifactPromoteResponse(
        artifact_id=artifact_id,
        promoted_attribute_ids=[
            str(value) for value in cast(list[object], result["promoted_attribute_ids"])
        ],
        promoted_preference_signal_ids=[
            str(value) for value in cast(list[object], result["promoted_preference_signal_ids"])
        ],
        analysis=_analysis_response(artifact_id, cast(dict[str, object], result["analysis"])),
    )


@router.get("/artifacts/{artifact_id}/analysis", response_model=ArtifactAnalysisResponse)
def artifact_analysis(artifact_id: str) -> ArtifactAnalysisResponse:
    """Return the last persisted analysis payload for one artifact."""
    with get_db_connection() as conn:
        if get_artifact_record(conn, artifact_id) is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        analysis = get_artifact_analysis(conn, artifact_id)
    return _analysis_response(artifact_id, cast(dict[str, object] | None, analysis))
