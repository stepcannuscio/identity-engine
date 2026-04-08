"""Public query-engine interface.

This module ties together classification, retrieval, prompt assembly, and LLM
response generation, while keeping all session state in the Session object.
"""

from __future__ import annotations

from config.llm_router import generate_response
from engine.prompt_builder import build_prompt
from engine.query_classifier import classify_query
from engine.retriever import retrieve_attributes
from engine.session import Session


def query(
    user_query: str,
    session: Session,
    conn,
    provider_config,
) -> str:
    """Run one end-to-end query and update only in-memory session state."""
    query_type = classify_query(user_query)
    attributes = retrieve_attributes(user_query, query_type, conn)

    backend = "local" if getattr(provider_config, "is_local", False) else str(
        getattr(provider_config, "provider", "unknown")
    )

    messages = build_prompt(
        user_query,
        attributes,
        session.get_history(),
        query_type,
        target_backend=backend,
    )

    response = generate_response(messages, provider_config)
    session.add_exchange(user_query, response)
    session.query_count += 1
    session.attributes_retrieved += len(attributes)
    session.log_query(user_query, query_type, backend, len(attributes))

    return response
