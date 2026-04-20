"""Deterministic artifact chunk retrieval."""

from __future__ import annotations

import json
import re

from engine.retriever import DOMAIN_KEYWORDS, STOPWORDS
from engine.text_utils import tokenize

DEFAULT_ARTIFACT_LIMIT = 3
_ENUMERATION_RE = re.compile(r"\b(?:top|favorite|\d+)\b")
_QUERY_SYNONYMS = {
    "cook": {"meal", "dinner", "food", "recipe", "cooking"},
    "cooked": {"meal", "dinner", "food", "recipe", "cooking"},
    "cooking": {"meal", "dinner", "food", "recipe", "cook"},
    "dinner": {"meal", "recipe", "cook", "cooking"},
    "meal": {"dinner", "recipe", "cook", "cooking"},
    "read": {"book", "reading", "article", "notes"},
    "recipe": {"dinner", "meal", "cook", "cooking"},
    "recipes": {"dinner", "meal", "cook", "cooking"},
    "wrote": {"writing", "draft", "notes", "journal"},
    "writing": {"draft", "wrote", "journal", "notes"},
}
_LISTISH_MARKERS = ("- ", "* ", "\n1.", "\n2.", ",", "ingredients", "instructions")


def _tokenize(text: str) -> set[str]:
    return tokenize(text, stopwords=STOPWORDS)


def _expand_query_tokens(query: str) -> set[str]:
    tokens = _tokenize(query)
    expanded = set(tokens)
    for token in tokens:
        expanded.update(_QUERY_SYNONYMS.get(token, set()))
    return expanded


def _metadata_descriptor_text(metadata: dict[str, object]) -> str:
    parts: list[str] = []
    filename = str(metadata.get("filename", "")).strip()
    if filename:
        parts.append(filename)
    analysis = metadata.get("analysis")
    if isinstance(analysis, dict):
        summary = str(analysis.get("summary", "")).strip()
        content_kind = str(analysis.get("content_kind", "")).strip()
        descriptor_tokens = analysis.get("descriptor_tokens")
        if content_kind:
            parts.append(content_kind)
        if summary:
            parts.append(summary)
        if isinstance(descriptor_tokens, list):
            parts.extend(str(token).strip() for token in descriptor_tokens if str(token).strip())
    return " ".join(parts)


def _is_enumeration_query(query: str) -> bool:
    lowered = query.lower()
    return "list my" in lowered or bool(_ENUMERATION_RE.search(lowered))


def _list_like_bonus(query: str, content: str, metadata: dict[str, object]) -> float:
    if not _is_enumeration_query(query):
        return 0.0
    bonus = 0.0
    lowered = content.lower()
    if sum(lowered.count(marker) for marker in _LISTISH_MARKERS) >= 2:
        bonus += 0.6
    analysis = metadata.get("analysis")
    if isinstance(analysis, dict):
        if str(analysis.get("content_kind", "")).strip() in {
            "recipe_collection",
            "list",
            "reading_log",
            "journal_log",
        }:
            bonus += 0.8
    return bonus


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
    query_tokens = _expand_query_tokens(query)
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
        artifact_metadata = json.loads(row[8]) if row[8] else {}
        metadata_text = _metadata_descriptor_text(artifact_metadata)
        title_tokens = _tokenize(title)
        content_tokens = _tokenize(content)
        tag_tokens = _tokenize(tags)
        metadata_tokens = _tokenize(metadata_text)
        overlap = len(query_tokens.intersection(content_tokens))
        title_overlap = len(query_tokens.intersection(title_tokens))
        tag_overlap = len(query_tokens.intersection(tag_tokens))
        metadata_overlap = len(query_tokens.intersection(metadata_tokens))
        if overlap <= 0:
            if title_overlap <= 0 and tag_overlap <= 0 and metadata_overlap <= 0:
                continue

        domain_bonus = 2 if domain and domain in matched_domains else 0
        title_bonus = title_overlap * 2
        tag_bonus = tag_overlap * 1.2
        metadata_bonus = metadata_overlap * 1.5
        score = (
            (overlap * 1.4)
            + domain_bonus
            + title_bonus
            + tag_bonus
            + metadata_bonus
            + _list_like_bonus(query, content, artifact_metadata)
        )
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
                "artifact_metadata": artifact_metadata,
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
