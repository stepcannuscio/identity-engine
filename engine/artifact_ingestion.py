"""Local-only artifact ingestion helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import uuid

from db.evidence import register_artifact_evidence

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


def parse_artifact_metadata(raw_metadata: str | None) -> dict[str, object]:
    """Return one artifact metadata payload as a dictionary."""
    if not raw_metadata:
        return {}
    try:
        value = json.loads(raw_metadata)
    except json.JSONDecodeError:
        return {}
    if isinstance(value, dict):
        return value
    return {}


def serialize_artifact_metadata(metadata: dict[str, object] | None) -> str | None:
    """Serialize artifact metadata when it has meaningful content."""
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True)


def get_artifact_record(conn, artifact_id: str):
    """Return one stored artifact row and parsed metadata."""
    row = conn.execute(
        """
        SELECT
            a.id,
            a.title,
            a.type,
            a.source,
            a.content,
            a.metadata,
            d.name
        FROM artifacts a
        LEFT JOIN domains d ON d.id = a.domain_id
        WHERE a.id = ?
        """,
        (artifact_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "title": str(row[1]),
        "type": str(row[2]),
        "source": str(row[3]),
        "content": str(row[4]),
        "metadata": parse_artifact_metadata(row[5]),
        "domain": str(row[6]) if row[6] is not None else None,
    }


def get_artifact_tags(conn, artifact_id: str) -> list[str]:
    """Return normalized artifact tags in stable order."""
    rows = conn.execute(
        """
        SELECT tag
        FROM artifact_tags
        WHERE artifact_id = ?
        ORDER BY tag ASC
        """,
        (artifact_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def update_artifact_metadata(conn, artifact_id: str, metadata: dict[str, object]) -> dict[str, object]:
    """Persist one artifact metadata payload and return the stored value."""
    normalized = dict(metadata)
    conn.execute(
        "UPDATE artifacts SET metadata = ? WHERE id = ?",
        (serialize_artifact_metadata(normalized), artifact_id),
    )
    conn.commit()
    return normalized


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
    tags: list[str] | None = None,
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
    metadata_json = serialize_artifact_metadata(metadata_payload)

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
    normalized_tags = sorted(
        {
            tag.strip().lower()
            for tag in (tags or [])
            if isinstance(tag, str) and tag.strip()
        }
    )
    if normalized_tags:
        conn.executemany(
            """
            INSERT INTO artifact_tags (id, artifact_id, tag, created_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                (str(uuid.uuid4()), artifact_id, tag, created_at)
                for tag in normalized_tags
            ],
        )
    register_artifact_evidence(conn, artifact_id=artifact_id)
    conn.commit()
    return ArtifactIngestResult(artifact_id=artifact_id, chunk_count=len(chunks))
