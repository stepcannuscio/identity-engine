"""Deterministic artifact chunk retrieval."""

from __future__ import annotations

import json

from engine.retriever import DOMAIN_KEYWORDS, STOPWORDS
from engine.text_utils import tokenize

DEFAULT_ARTIFACT_LIMIT = 3


def _tokenize(text: str) -> set[str]:
    return tokenize(text, stopwords=STOPWORDS)


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
    domain_hints: list[str] | None = None,
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
            d.name,
            GROUP_CONCAT(t.tag, ' ')
        FROM artifact_chunks c
        JOIN artifacts a ON a.id = c.artifact_id
        LEFT JOIN domains d ON d.id = a.domain_id
        LEFT JOIN artifact_tags t ON t.artifact_id = a.id
        WHERE 1 = 1 {type_clause}
        GROUP BY
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
        ORDER BY a.created_at DESC, c.chunk_index ASC
        """,
        params,
    ).fetchall()

    matched_domains = set(domain_hints or []) or _query_domains(query)
    scored: list[dict] = []
    for row in rows:
        content = str(row[3])
        title = str(row[5])
        domain = str(row[9]) if row[9] is not None else None
        tags = str(row[10]) if row[10] is not None else ""
        haystack_tokens = _tokenize(f"{title} {content} {tags}")
        title_tokens = _tokenize(title)
        content_tokens = _tokenize(content)
        tag_tokens = _tokenize(tags)
        overlap = len(query_tokens.intersection(content_tokens))
        title_overlap = len(query_tokens.intersection(title_tokens))
        tag_overlap = len(query_tokens.intersection(tag_tokens))
        if overlap <= 0:
            if title_overlap <= 0 and tag_overlap <= 0:
                continue

        domain_bonus = 2 if domain and domain in matched_domains else 0
        title_bonus = title_overlap * 2
        tag_bonus = tag_overlap
        score = (overlap * 1.4) + domain_bonus + title_bonus + tag_bonus
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
