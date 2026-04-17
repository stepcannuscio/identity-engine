"""Local-only artifact ingestion helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import uuid

DEFAULT_CHUNK_WORD_TARGET = 450
TITLE_MAX_LENGTH = 120
_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class ArtifactChunk:
    """One stored artifact chunk."""

    id: str
    artifact_id: str
    chunk_index: int
    content: str
    metadata: str | None
    created_at: str


@dataclass(frozen=True)
class ArtifactIngestResult:
    """Summary of one completed artifact ingestion."""

    artifact_id: str
    chunk_count: int


def normalize_artifact_text(text: str) -> str:
    """Normalize line endings and trim outer whitespace."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("Artifact content cannot be empty.")
    return normalized


def _resolve_domain_id(conn, domain: str | None) -> str | None:
    if domain is None:
        return None

    normalized = domain.strip()
    if not normalized:
        return None

    row = conn.execute("SELECT id FROM domains WHERE name = ?", (normalized,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown artifact domain: {normalized}")
    return str(row[0])


def _default_title(content: str, filename: str | None) -> str:
    if filename:
        name = Path(filename).stem.strip()
        if name:
            return name[:TITLE_MAX_LENGTH]

    first_line = content.splitlines()[0].strip()
    if first_line:
        return first_line[:TITLE_MAX_LENGTH]
    return "Untitled artifact"


def chunk_artifact(content: str, *, target_words: int = DEFAULT_CHUNK_WORD_TARGET) -> list[str]:
    """Split artifact content into ordered chunks without overlap."""
    normalized = normalize_artifact_text(content)
    words = _WORD_RE.findall(normalized)
    if not words:
        raise ValueError("Artifact content cannot be empty.")

    if len(words) <= target_words:
        return [normalized]

    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        if len(current) >= target_words:
            chunks.append(" ".join(current))
            current = []

    if current:
        chunks.append(" ".join(current))
    return chunks


def ingest_artifact(
    conn,
    *,
    text: str,
    title: str | None = None,
    artifact_type: str = "note",
    source: str = "capture",
    domain: str | None = None,
    filename: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ArtifactIngestResult:
    """Store an artifact and its ordered chunks locally."""
    content = normalize_artifact_text(text)
    domain_id = _resolve_domain_id(conn, domain)
    artifact_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    resolved_title = (title or "").strip() or _default_title(content, filename)
    chunks = chunk_artifact(content)
    metadata_payload = dict(metadata or {})
    if filename:
        metadata_payload.setdefault("filename", filename)
    metadata_json = json.dumps(metadata_payload, sort_keys=True) if metadata_payload else None

    conn.execute(
        """
        INSERT INTO artifacts (
            id,
            domain_id,
            type,
            title,
            source,
            content,
            metadata,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            domain_id,
            artifact_type.strip() or "note",
            resolved_title,
            source.strip() or "capture",
            content,
            metadata_json,
            created_at,
        ),
    )

    chunk_rows: list[tuple[str, str, int, str, str | None, str]] = []
    for index, chunk in enumerate(chunks):
        chunk_rows.append(
            (
                str(uuid.uuid4()),
                artifact_id,
                index,
                chunk,
                json.dumps({"word_count": len(_WORD_RE.findall(chunk))}, sort_keys=True),
                created_at,
            )
        )

    conn.executemany(
        """
        INSERT INTO artifact_chunks (
            id,
            artifact_id,
            chunk_index,
            content,
            metadata,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        chunk_rows,
    )
    conn.commit()
    return ArtifactIngestResult(artifact_id=artifact_id, chunk_count=len(chunks))
