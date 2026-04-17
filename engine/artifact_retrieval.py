"""Deterministic artifact chunk retrieval."""

from __future__ import annotations

import json
import re

from engine.retriever import DOMAIN_KEYWORDS, STOPWORDS

DEFAULT_ARTIFACT_LIMIT = 3
_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _normalize_token(token: str) -> str:
    normalized = token.lower()
    if len(normalized) > 5 and normalized.endswith("ing"):
        normalized = normalized[:-3]
    elif len(normalized) > 4 and normalized.endswith("ed"):
        normalized = normalized[:-2]
    if len(normalized) > 4 and normalized.endswith("s"):
        normalized = normalized[:-1]
    return normalized


def _tokenize(text: str) -> set[str]:
    return {
        _normalize_token(token)
        for token in _TOKEN_RE.findall(text.lower())
        if _normalize_token(token) not in STOPWORDS
    }


def _query_domains(query: str) -> set[str]:
    lowered = query.lower()
    matched: set[str] = set()
    for domain, triggers in DOMAIN_KEYWORDS.items():
        if any(trigger in lowered for trigger in triggers):
            matched.add(domain)
    return matched


def retrieve_artifact_chunk_candidates(
    conn,
    query: str,
    *,
    limit: int | None = DEFAULT_ARTIFACT_LIMIT,
    artifact_type: str | None = None,
) -> list[dict]:
    """Return scored artifact chunk candidates for a query."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    params: list[object] = []
    type_clause = ""
    if artifact_type:
        type_clause = "AND a.type = ?"
        params.append(artifact_type)

    rows = conn.execute(
        f"""
        SELECT
            c.id,
            c.artifact_id,
            c.chunk_index,
            c.content,
            c.metadata,
            a.title,
            a.type,
            a.source,
            a.metadata,
            d.name
        FROM artifact_chunks c
        JOIN artifacts a ON a.id = c.artifact_id
        LEFT JOIN domains d ON d.id = a.domain_id
        WHERE 1 = 1 {type_clause}
        ORDER BY a.created_at DESC, c.chunk_index ASC
        """,
        params,
    ).fetchall()

    matched_domains = _query_domains(query)
    scored: list[dict] = []
    for row in rows:
        content = str(row[3])
        title = str(row[5])
        domain = str(row[9]) if row[9] is not None else None
        haystack_tokens = _tokenize(f"{title} {content}")
        overlap = len(query_tokens.intersection(haystack_tokens))
        if overlap <= 0:
            continue

        domain_bonus = 1 if domain and domain in matched_domains else 0
        title_bonus = 1 if any(token in _tokenize(title) for token in query_tokens) else 0
        score = overlap + domain_bonus + title_bonus
        scored.append(
            {
                "id": str(row[0]),
                "artifact_id": str(row[1]),
                "chunk_index": int(row[2]),
                "content": content,
                "chunk_metadata": json.loads(row[4]) if row[4] else {},
                "title": title,
                "type": str(row[6]),
                "source": str(row[7]),
                "artifact_metadata": json.loads(row[8]) if row[8] else {},
                "domain": domain,
                "routing": "local_only",
                "score": float(score),
            }
        )

    scored.sort(
        key=lambda item: (
            item["score"],
            -int(item["chunk_index"]),
        ),
        reverse=True,
    )
    if limit is None:
        return scored
    return scored[:limit]


def retrieve_artifact_chunks(
    conn,
    query: str,
    *,
    limit: int = DEFAULT_ARTIFACT_LIMIT,
    artifact_type: str | None = None,
) -> list[dict]:
    """Return the most relevant artifact chunks for a query."""
    return retrieve_artifact_chunk_candidates(
        conn,
        query,
        limit=limit,
        artifact_type=artifact_type,
    )


def get_artifact_chunks_by_id(conn, artifact_id: str) -> list[dict]:
    """Return stored chunks for one artifact in order."""
    rows = conn.execute(
        """
        SELECT id, artifact_id, chunk_index, content, metadata
        FROM artifact_chunks
        WHERE artifact_id = ?
        ORDER BY chunk_index ASC, id ASC
        """,
        (artifact_id,),
    ).fetchall()

    return [
        {
            "id": str(row[0]),
            "artifact_id": str(row[1]),
            "chunk_index": int(row[2]),
            "content": str(row[3]),
            "metadata": json.loads(row[4]) if row[4] else {},
        }
        for row in rows
    ]
