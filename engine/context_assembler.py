"""Structured context assembly for inference tasks.

This module packages retrieved identity data into a typed object that can be
passed through privacy checks and prompt rendering without spreading selection
logic across multiple layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.artifact_retrieval import DEFAULT_ARTIFACT_LIMIT, retrieve_artifact_chunks
from engine.preference_summary import PreferenceSummaryPayload, empty_preference_summary
from engine.preference_summary import (
    get_relevant_preference_context,
    is_preference_attribute,
)
from engine.retriever import budget_for_query_type, retrieve_attributes
from engine.session import HISTORY_CAP


@dataclass(frozen=True)
class AssembledContext:
    """Inference-ready identity context for one task."""

    task_type: str
    input_text: str
    attributes: list[dict]
    session_history: list[dict]
    domains_used: list[str]
    attribute_count: int
    retrieval_mode: str
    was_trimmed: bool
    contains_local_only: bool
    preference_attributes: list[dict] = field(default_factory=list)
    preference_summary: PreferenceSummaryPayload = field(
        default_factory=empty_preference_summary
    )
    preference_count: int = 0
    preference_categories_used: list[str] = field(default_factory=list)
    artifact_chunks: list[dict] = field(default_factory=list)
    artifact_count: int = 0
    artifact_sources: list[str] = field(default_factory=list)
    budget_metadata: dict[str, int | float] = field(default_factory=dict)


def _should_retrieve_artifacts(
    query_type: str,
    attributes: list[dict],
    preference_count: int,
) -> bool:
    if query_type == "open_ended":
        return True
    return len(attributes) <= 2 and preference_count == 0


def _cap_session_history(history: list[dict]) -> tuple[list[dict], bool]:
    if not history:
        return [], False

    max_messages = HISTORY_CAP * 2
    trimmed = len(history) > max_messages
    return history[-max_messages:], trimmed


def assemble_query_context(
    query: str,
    query_type: str,
    session_history: list[dict],
    conn,
) -> AssembledContext:
    """Assemble structured context for grounded query inference."""
    retrieved_attributes = retrieve_attributes(query, query_type, conn)
    attributes = [
        attribute
        for attribute in retrieved_attributes
        if not is_preference_attribute(attribute)
    ]
    preference_context = get_relevant_preference_context(query, query_type, conn)
    artifact_chunks: list[dict] = []
    if _should_retrieve_artifacts(query_type, attributes, preference_context.item_count):
        artifact_chunks = retrieve_artifact_chunks(
            conn,
            query,
            limit=DEFAULT_ARTIFACT_LIMIT,
        )
    capped_history, history_was_trimmed = _cap_session_history(session_history)
    domains_used = sorted(
        {
            str(attribute.get("domain", ""))
            for attribute in attributes + preference_context.attributes + artifact_chunks
            if attribute.get("domain")
        }
    )
    budget = budget_for_query_type(query_type)
    contains_local_only = any(
        attribute.get("routing") == "local_only" for attribute in attributes
    ) or any(
        attribute.get("routing") == "local_only"
        for attribute in preference_context.attributes
    ) or bool(artifact_chunks)
    artifact_sources = sorted(
        {
            str(chunk.get("title", "")).strip()
            for chunk in artifact_chunks
            if str(chunk.get("title", "")).strip()
        }
    )
    was_trimmed = (
        history_was_trimmed
        or len(retrieved_attributes) >= int(budget["max_attributes"])
        or preference_context.was_trimmed
        or len(artifact_chunks) >= DEFAULT_ARTIFACT_LIMIT
    )

    return AssembledContext(
        task_type="query",
        input_text=query,
        attributes=attributes,
        session_history=capped_history,
        domains_used=domains_used,
        attribute_count=len(attributes),
        retrieval_mode=query_type,
        was_trimmed=was_trimmed,
        contains_local_only=contains_local_only,
        preference_attributes=preference_context.attributes,
        preference_summary=preference_context.summary,
        preference_count=preference_context.item_count,
        preference_categories_used=preference_context.categories_used,
        artifact_chunks=artifact_chunks,
        artifact_count=len(artifact_chunks),
        artifact_sources=artifact_sources,
        budget_metadata={
            "max_attributes": int(budget["max_attributes"]),
            "max_domains": int(budget["max_domains"]),
            "score_threshold": float(budget["score_threshold"]),
            "history_cap_messages": HISTORY_CAP * 2,
            "max_artifact_chunks": DEFAULT_ARTIFACT_LIMIT,
            **preference_context.budget_metadata,
        },
    )
