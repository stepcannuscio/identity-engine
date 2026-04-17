"""Artifact ingestion API routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import BinaryIO, Protocol, TypeGuard

from fastapi import APIRouter, HTTPException, Request

from engine.artifact_ingestion import ingest_artifact
from engine.local_document_parser import extract_docx_text, extract_pdf_text
from server.db import get_db_connection
from server.models.schemas import ArtifactIngestResponse

router = APIRouter(tags=["artifacts"])

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ""}
_DOCX_SUFFIXES = {".docx"}
_PDF_SUFFIXES = {".pdf"}


class _UploadLike(Protocol):
    filename: str | None
    file: BinaryIO


def _decode_upload(upload: _UploadLike) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in _TEXT_SUFFIXES | _DOCX_SUFFIXES | _PDF_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported artifact file type")

    raw = upload.file.read()
    if suffix in _PDF_SUFFIXES:
        text = extract_pdf_text(raw)
        if not text.strip():
            raise HTTPException(status_code=400, detail="unable to extract text from pdf")
        return text
    if suffix in _DOCX_SUFFIXES:
        try:
            text = extract_docx_text(raw)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="unable to extract text from docx") from exc
        if not text.strip():
            raise HTTPException(status_code=400, detail="unable to extract text from docx")
        return text
    try:
        return raw.decode("utf-8")
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


@router.post("/artifacts", response_model=ArtifactIngestResponse)
async def create_artifact(request: Request) -> ArtifactIngestResponse:
    """Ingest a local artifact from JSON text or multipart upload."""
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
        payload = await request.json()
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
    )
