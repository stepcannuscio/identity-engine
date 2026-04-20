"""Generalized privacy-safe evidence indexing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
import uuid


@dataclass(frozen=True, slots=True)
class EvidenceLinkInput:
    """One target linked to a generalized evidence record."""

    target_type: str
    target_id: str


@dataclass(frozen=True, slots=True)
class EvidenceRecordInput:
    """One generalized evidence record to store."""

    kind: str
    source_type: str
    routing: str
    summary: str
    source_ref: str | None
    metadata: dict[str, object] | None
    origin_table: str
    origin_id: str
    created_at: str
    links: list[EvidenceLinkInput]


@dataclass(frozen=True, slots=True)
class EvidenceSummaryRecord:
    """One privacy-safe evidence item returned for an API target."""

    id: str
    kind: str
    source_type: str
    routing: str
    summary: str
    source_ref: str | None
    metadata: dict[str, object] | None
    created_at: str


_TARGET_TYPES = {"attribute", "artifact", "session", "query_feedback", "voice_feedback"}
_SOURCE_LABELS = {
    "capture": "captured note",
    "journal": "journal entry",
    "reflection_session": "reflection session",
}
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _serialize_metadata(metadata: dict[str, object] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True)


def _parse_metadata(raw_metadata: str | None) -> dict[str, object] | None:
    if not raw_metadata:
        return None
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def _source_label(source_type: str) -> str:
    normalized = source_type.strip().lower()
    if normalized in _SOURCE_LABELS:
        return _SOURCE_LABELS[normalized]
    return normalized.replace("_", " ") or "local evidence"


def _require_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Evidence {field} is required.")
    return normalized


def _normalize_links(links: list[EvidenceLinkInput]) -> list[EvidenceLinkInput]:
    normalized: list[EvidenceLinkInput] = []
    seen: set[tuple[str, str]] = set()
    for link in links:
        target_type = _require_text(link.target_type, field="target_type")
        target_id = _require_text(link.target_id, field="target_id")
        if target_type not in _TARGET_TYPES:
            raise ValueError(f"Unsupported evidence target_type: {target_type}")
        key = (target_type, target_id)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(EvidenceLinkInput(target_type=target_type, target_id=target_id))
    if not normalized:
        raise ValueError("Generalized evidence requires at least one target link.")
    return normalized


def register_evidence_record(conn, payload: EvidenceRecordInput) -> str:
    """Insert or refresh one generalized evidence record and its links."""
    record_id = str(uuid.uuid4())
    links = _normalize_links(payload.links)
    created_at = payload.created_at or datetime.now(UTC).isoformat()
    metadata_json = _serialize_metadata(payload.metadata)

    conn.execute(
        """
        INSERT INTO evidence_records (
            id,
            kind,
            source_type,
            routing,
            summary,
            source_ref,
            metadata_json,
            origin_table,
            origin_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(origin_table, origin_id) DO UPDATE SET
            kind = excluded.kind,
            source_type = excluded.source_type,
            routing = excluded.routing,
            summary = excluded.summary,
            source_ref = excluded.source_ref,
            metadata_json = excluded.metadata_json,
            created_at = excluded.created_at
        """,
        (
            record_id,
            _require_text(payload.kind, field="kind"),
            _require_text(payload.source_type, field="source_type"),
            payload.routing,
            _require_text(payload.summary, field="summary"),
            payload.source_ref,
            metadata_json,
            _require_text(payload.origin_table, field="origin_table"),
            _require_text(payload.origin_id, field="origin_id"),
            created_at,
        ),
    )
    row = conn.execute(
        """
        SELECT id
        FROM evidence_records
        WHERE origin_table = ? AND origin_id = ?
        """,
        (payload.origin_table, payload.origin_id),
    ).fetchone()
    assert row is not None
    evidence_id = str(row[0])

    conn.executemany(
        """
        INSERT OR IGNORE INTO evidence_links (
            id,
            evidence_id,
            target_type,
            target_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (str(uuid.uuid4()), evidence_id, link.target_type, link.target_id, created_at)
            for link in links
        ],
    )
    return evidence_id


def list_evidence_for_target(
    conn,
    *,
    target_type: str,
    target_id: str,
    kind: str | None = None,
) -> list[EvidenceSummaryRecord]:
    """Return privacy-safe evidence records linked to one target."""
    if target_type not in _TARGET_TYPES:
        raise ValueError(f"Unsupported evidence target_type: {target_type}")
    params: list[object] = [target_type, target_id]
    kind_where = ""
    if kind:
        kind_where = " AND er.kind = ?"
        params.append(kind.strip())
    rows = conn.execute(
        f"""
        SELECT DISTINCT
            er.id,
            er.kind,
            er.source_type,
            er.routing,
            er.summary,
            er.source_ref,
            er.metadata_json,
            er.created_at
        FROM evidence_records er
        JOIN evidence_links el ON el.evidence_id = er.id
        WHERE el.target_type = ? AND el.target_id = ?{kind_where}
        ORDER BY er.created_at ASC, er.id ASC
        """,
        tuple(params),
    ).fetchall()
    return [
        EvidenceSummaryRecord(
            id=str(row[0]),
            kind=str(row[1]),
            source_type=str(row[2]),
            routing=str(row[3]),
            summary=str(row[4]),
            source_ref=str(row[5]) if row[5] is not None else None,
            metadata=_parse_metadata(row[6]),
            created_at=str(row[7]),
        )
        for row in rows
    ]


def register_artifact_evidence(conn, *, artifact_id: str) -> str | None:
    """Register one artifact as generalized local evidence."""
    row = conn.execute(
        """
        SELECT
            a.id,
            a.type,
            a.source,
            d.name,
            a.created_at,
            COUNT(DISTINCT c.id),
            COUNT(DISTINCT t.id)
        FROM artifacts a
        LEFT JOIN domains d ON d.id = a.domain_id
        LEFT JOIN artifact_chunks c ON c.artifact_id = a.id
        LEFT JOIN artifact_tags t ON t.artifact_id = a.id
        WHERE a.id = ?
        GROUP BY a.id, a.type, a.source, d.name, a.created_at
        """,
        (artifact_id,),
    ).fetchone()
    if row is None:
        return None

    chunk_count = int(row[5] or 0)
    payload = EvidenceRecordInput(
        kind="artifact",
        source_type=str(row[2]),
        routing="local_only",
        summary=f"Local artifact stored from {row[2]} as {row[1]} with {chunk_count} chunk(s).",
        source_ref=str(row[0]),
        metadata={
            "artifact_type": str(row[1]),
            "domain": str(row[3]) if row[3] is not None else None,
            "chunk_count": chunk_count,
            "tag_count": int(row[6] or 0),
        },
        origin_table="artifacts",
        origin_id=str(row[0]),
        created_at=str(row[4]),
        links=[EvidenceLinkInput(target_type="artifact", target_id=str(row[0]))],
    )
    return register_evidence_record(conn, payload)


def register_inference_evidence_record(conn, *, evidence_id: str) -> str | None:
    """Register one inference-evidence row in the generalized evidence index."""
    row = conn.execute(
        """
        SELECT
            ie.id,
            ie.attribute_id,
            ie.source_type,
            ie.source_ref,
            ie.supporting_text,
            ie.weight,
            ie.created_at
        FROM inference_evidence ie
        WHERE ie.id = ?
        """,
        (evidence_id,),
    ).fetchone()
    if row is None:
        return None

    supporting_text = str(row[4]) if row[4] is not None else None
    word_count = _word_count(supporting_text)
    label = _source_label(str(row[2]))
    if word_count > 0:
        summary = f"Derived from {label}; {word_count}-word supporting note kept local."
    elif row[3]:
        summary = f"Derived from {label}; linked local reference retained."
    else:
        summary = f"Derived from {label}; supporting detail retained locally."

    payload = EvidenceRecordInput(
        kind="inference_evidence",
        source_type=str(row[2]),
        routing="local_only",
        summary=summary,
        source_ref=str(row[3]) if row[3] is not None else None,
        metadata={
            "word_count": word_count,
            "has_source_ref": row[3] is not None,
            "weight": None if row[5] is None else float(row[5]),
        },
        origin_table="inference_evidence",
        origin_id=str(row[0]),
        created_at=str(row[6]),
        links=[EvidenceLinkInput(target_type="attribute", target_id=str(row[1]))],
    )
    return register_evidence_record(conn, payload)


def register_query_feedback_evidence(conn, *, feedback_id: str) -> str | None:
    """Register one query-feedback row in the generalized evidence index."""
    row = conn.execute(
        """
        SELECT
            id,
            session_id,
            feedback,
            backend,
            query_type,
            source_profile,
            confidence,
            created_at
        FROM query_feedback
        WHERE id = ?
        """,
        (feedback_id,),
    ).fetchone()
    if row is None:
        return None

    links = [EvidenceLinkInput(target_type="query_feedback", target_id=str(row[0]))]
    if row[1] is not None:
        links.append(EvidenceLinkInput(target_type="session", target_id=str(row[1])))

    payload = EvidenceRecordInput(
        kind="query_feedback",
        source_type="user_feedback",
        routing="local_only",
        summary=f"Local query feedback marked as {row[2]} for a {row[3]} response.",
        source_ref=str(row[0]),
        metadata={
            "backend": str(row[3]),
            "query_type": str(row[4]),
            "source_profile": str(row[5]),
            "confidence": str(row[6]),
        },
        origin_table="query_feedback",
        origin_id=str(row[0]),
        created_at=str(row[7]),
        links=links,
    )
    return register_evidence_record(conn, payload)


def register_voice_feedback_evidence(conn, *, feedback_id: str) -> str | None:
    """Register one voice-feedback row in the generalized evidence index."""
    row = conn.execute(
        """
        SELECT
            id,
            session_id,
            query_feedback_id,
            feedback,
            backend,
            query_type,
            source_profile,
            created_at
        FROM voice_feedback
        WHERE id = ?
        """,
        (feedback_id,),
    ).fetchone()
    if row is None:
        return None

    links = [EvidenceLinkInput(target_type="voice_feedback", target_id=str(row[0]))]
    if row[1] is not None:
        links.append(EvidenceLinkInput(target_type="session", target_id=str(row[1])))
    if row[2] is not None:
        links.append(EvidenceLinkInput(target_type="query_feedback", target_id=str(row[2])))

    payload = EvidenceRecordInput(
        kind="voice_feedback",
        source_type="voice_feedback",
        routing="local_only",
        summary=f"Local voice feedback marked the response as {row[3]}.",
        source_ref=str(row[0]),
        metadata={
            "backend": str(row[4]),
            "query_type": str(row[5]),
            "source_profile": str(row[6]),
        },
        origin_table="voice_feedback",
        origin_id=str(row[0]),
        created_at=str(row[7]),
        links=links,
    )
    return register_evidence_record(conn, payload)


def backfill_generalized_evidence(conn) -> None:
    """Populate generalized evidence rows for pre-existing source tables."""
    artifact_rows = conn.execute("SELECT id FROM artifacts ORDER BY created_at ASC, id ASC").fetchall()
    for row in artifact_rows:
        register_artifact_evidence(conn, artifact_id=str(row[0]))

    inference_rows = conn.execute(
        "SELECT id FROM inference_evidence ORDER BY created_at ASC, id ASC"
    ).fetchall()
    for row in inference_rows:
        register_inference_evidence_record(conn, evidence_id=str(row[0]))

    query_rows = conn.execute(
        "SELECT id FROM query_feedback ORDER BY created_at ASC, id ASC"
    ).fetchall()
    for row in query_rows:
        register_query_feedback_evidence(conn, feedback_id=str(row[0]))

    voice_rows = conn.execute(
        "SELECT id FROM voice_feedback ORDER BY created_at ASC, id ASC"
    ).fetchall()
    for row in voice_rows:
        register_voice_feedback_evidence(conn, feedback_id=str(row[0]))
