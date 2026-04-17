"""Tests for artifact ingestion and retrieval helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.artifact_ingestion import chunk_artifact, ingest_artifact
from engine.artifact_retrieval import get_artifact_chunks_by_id, retrieve_artifact_chunks


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


def test_ingest_artifact_stores_artifact_and_ordered_chunks(conn):
    text = " ".join(f"word{i}" for i in range(950))

    result = ingest_artifact(
        conn,
        text=text,
        title="Weekly notes",
        artifact_type="document",
        source="upload",
        domain="goals",
        filename="weekly-notes.md",
    )

    stored = conn.execute(
        "SELECT title, type, source, content FROM artifacts WHERE id = ?",
        (result.artifact_id,),
    ).fetchone()
    assert stored == ("Weekly notes", "document", "upload", text)

    chunks = get_artifact_chunks_by_id(conn, result.artifact_id)
    assert result.chunk_count == len(chunks)
    assert len(chunks) == 3
    assert [chunk["chunk_index"] for chunk in chunks] == [0, 1, 2]


def test_chunk_artifact_preserves_order_without_overlap():
    text = " ".join(str(index) for index in range(12))

    chunks = chunk_artifact(text, target_words=5)

    assert chunks == [
        "0 1 2 3 4",
        "5 6 7 8 9",
        "10 11",
    ]


def test_retrieve_artifact_chunks_returns_relevant_matches(conn):
    first = ingest_artifact(
        conn,
        text="I keep detailed notes about writing tone and concise drafts.",
        title="Writing notes",
        artifact_type="note",
        source="capture",
        domain="voice",
    )
    ingest_artifact(
        conn,
        text="Trail running helps me think clearly after work.",
        title="Exercise log",
        artifact_type="journal",
        source="capture",
        domain="patterns",
    )

    results = retrieve_artifact_chunks(conn, "summarize my writing notes", limit=2)

    assert len(results) == 1
    assert results[0]["artifact_id"] == first.artifact_id
    assert results[0]["title"] == "Writing notes"
    assert results[0]["routing"] == "local_only"


def test_retrieve_artifact_chunks_respects_limit(conn):
    for index in range(4):
        ingest_artifact(
            conn,
            text=f"Project planning note {index} with roadmap detail and next steps.",
            title=f"Planning {index}",
            artifact_type="note",
            source="capture",
            domain="goals",
        )

    results = retrieve_artifact_chunks(conn, "planning next steps roadmap", limit=2)

    assert len(results) == 2
