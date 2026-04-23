"""Optional local-only similarity helpers for attribute retrieval."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from config.llm_router import generate_embedding
from engine.text_utils import cosine_similarity, stable_text_hash, tokenize

_EMBEDDING_MODEL = "nomic-embed-text"
_FTS_MAX_BONUS = 0.04
_EMBEDDING_MAX_BONUS = 0.06
_TOTAL_MAX_BONUS = 0.10
_EMBEDDING_SIMILARITY_FLOOR = 0.55


def _attribute_text(attribute: dict[str, Any]) -> str:
    return " ".join(
        str(attribute.get(key, "") or "")
        for key in ("label", "value", "elaboration")
    ).strip()


def _ensure_temp_fts(conn) -> bool:
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS temp.attribute_retrieval_fts
            USING fts5(attribute_id UNINDEXED, searchable_text)
            """
        )
        return True
    except sqlite3.OperationalError:
        return False


def _populate_temp_fts(conn, attributes: list[dict[str, Any]]) -> bool:
    if not _ensure_temp_fts(conn):
        return False
    conn.execute("DELETE FROM temp.attribute_retrieval_fts")
    conn.executemany(
        """
        INSERT INTO temp.attribute_retrieval_fts (attribute_id, searchable_text)
        VALUES (?, ?)
        """,
        [
            (str(attribute.get("id")), _attribute_text(attribute))
            for attribute in attributes
            if attribute.get("id")
        ],
    )
    return True


def _fts_query_string(query: str) -> str:
    tokens = sorted(tokenize(query))
    if not tokens:
        return ""
    return " OR ".join(tokens)


def _fts_bonus(conn, query: str, attributes: list[dict[str, Any]]) -> dict[str, float]:
    if not attributes or not _populate_temp_fts(conn, attributes):
        return {}
    match_query = _fts_query_string(query)
    if not match_query:
        return {}

    try:
        rows = conn.execute(
            """
            SELECT attribute_id, bm25(attribute_retrieval_fts) AS rank
            FROM temp.attribute_retrieval_fts
            WHERE attribute_retrieval_fts MATCH ?
            ORDER BY rank ASC
            LIMIT 12
            """,
            (match_query,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    if not rows:
        return {}

    raw_scores = {str(row[0]): max(0.0, -float(row[1])) for row in rows}
    peak = max(raw_scores.values()) or 1.0
    return {
        attribute_id: round((score / peak) * _FTS_MAX_BONUS, 4)
        for attribute_id, score in raw_scores.items()
    }


def _load_cached_embedding(
    conn,
    *,
    attribute_id: str,
    text_hash: str,
    model: str,
) -> list[float] | None:
    row = conn.execute(
        """
        SELECT embedding_json
        FROM attribute_embedding_cache
        WHERE attribute_id = ? AND text_hash = ? AND embedding_model = ?
        """,
        (attribute_id, text_hash, model),
    ).fetchone()
    if row is None:
        return None
    try:
        parsed = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [float(value) for value in parsed]


def _store_embedding(
    conn,
    *,
    attribute_id: str,
    text_hash: str,
    model: str,
    embedding: list[float],
) -> None:
    conn.execute(
        """
        INSERT INTO attribute_embedding_cache (
            attribute_id,
            embedding_model,
            text_hash,
            embedding_json,
            updated_at
        )
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(attribute_id) DO UPDATE SET
            embedding_model = excluded.embedding_model,
            text_hash = excluded.text_hash,
            embedding_json = excluded.embedding_json,
            updated_at = excluded.updated_at
        """,
        (
            attribute_id,
            model,
            text_hash,
            json.dumps(embedding),
        ),
    )
    conn.commit()


def _attribute_embedding(
    conn,
    *,
    attribute: dict[str, Any],
    provider_config: Any,
) -> list[float] | None:
    attribute_id = str(attribute.get("id", "")).strip()
    if not attribute_id:
        return None
    text = _attribute_text(attribute)
    text_hash = stable_text_hash(text)
    cached = _load_cached_embedding(
        conn,
        attribute_id=attribute_id,
        text_hash=text_hash,
        model=_EMBEDDING_MODEL,
    )
    if cached is not None:
        return cached
    embedding = generate_embedding(text, provider_config, model=_EMBEDDING_MODEL)
    if embedding is None:
        return None
    _store_embedding(
        conn,
        attribute_id=attribute_id,
        text_hash=text_hash,
        model=_EMBEDDING_MODEL,
        embedding=embedding,
    )
    return embedding


def _embedding_bonus(
    conn,
    query: str,
    attributes: list[dict[str, Any]],
    *,
    provider_config: Any,
) -> dict[str, float]:
    query_embedding = generate_embedding(query, provider_config, model=_EMBEDDING_MODEL)
    if query_embedding is None:
        return {}

    raw_scores: dict[str, float] = {}
    for attribute in attributes:
        attribute_id = str(attribute.get("id", "")).strip()
        if not attribute_id:
            continue
        embedding = _attribute_embedding(conn, attribute=attribute, provider_config=provider_config)
        if embedding is None:
            continue
        similarity = cosine_similarity(query_embedding, embedding)
        if similarity < _EMBEDDING_SIMILARITY_FLOOR:
            continue
        raw_scores[attribute_id] = similarity

    if not raw_scores:
        return {}

    spread = max(raw_scores.values()) - _EMBEDDING_SIMILARITY_FLOOR
    if spread <= 0:
        spread = 1.0
    return {
        attribute_id: round(
            min(((score - _EMBEDDING_SIMILARITY_FLOOR) / spread) * _EMBEDDING_MAX_BONUS, _EMBEDDING_MAX_BONUS),
            4,
        )
        for attribute_id, score in raw_scores.items()
    }


def compute_similarity_bonus(
    conn,
    query: str,
    attributes: list[dict[str, Any]],
    *,
    provider_config: Any | None = None,
) -> dict[str, float]:
    """Return bounded optional retrieval bonuses from FTS and local embeddings."""
    if not attributes:
        return {}

    fts_scores = _fts_bonus(conn, query, attributes)
    embedding_scores = (
        _embedding_bonus(conn, query, attributes, provider_config=provider_config)
        if provider_config is not None
        else {}
    )

    combined: dict[str, float] = {}
    for attribute in attributes:
        attribute_id = str(attribute.get("id", "")).strip()
        if not attribute_id:
            continue
        combined[attribute_id] = round(
            min(
                fts_scores.get(attribute_id, 0.0) + embedding_scores.get(attribute_id, 0.0),
                _TOTAL_MAX_BONUS,
            ),
            4,
        )
    return combined
