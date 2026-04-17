"""Public query-engine interface.

This module ties together classification, retrieval, prompt assembly, and LLM
response generation, while keeping all session state in the Session object.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.context_assembler import AssembledContext, assemble_query_context
from engine.privacy_broker import PrivacyBroker
from engine.prompt_builder import build_prompt
from engine.query_classifier import classify_query
from engine.session import Session


@dataclass
class QueryContext:
    """Prepared query state used by both CLI and API entrypoints."""

    query: str
    query_type: str
    assembled_context: AssembledContext
    attributes: list[dict]
    messages: list[dict[str, str]]
    backend: str


def prepare_query(
    user_query: str,
    session: Session,
    conn,
    provider_config,
) -> QueryContext:
    """Prepare a query without generating a response yet."""
    query_type = classify_query(user_query)
    assembled_context = assemble_query_context(
        user_query,
        query_type,
        session.get_history(),
        conn,
    )

    backend = "local" if getattr(provider_config, "is_local", False) else "external"

    messages = build_prompt(assembled_context, target_backend=backend)

    return QueryContext(
        query=user_query,
        query_type=query_type,
        assembled_context=assembled_context,
        attributes=assembled_context.attributes,
        messages=messages,
        backend=backend,
    )


def record_query_result(session: Session, context: QueryContext, response: str) -> None:
    """Persist in-memory session metadata after a completed query."""
    session.add_exchange(context.query, response)
    session.query_count += 1
    session.attributes_retrieved += len(context.attributes)
    session.log_query(
        context.query,
        context.query_type,
        context.backend,
        len(context.attributes),
        context.assembled_context.domains_used,
    )


def query(
    user_query: str,
    session: Session,
    conn,
    provider_config,
) -> str:
    """Run one end-to-end query and update only in-memory session state."""
    context = prepare_query(user_query, session, conn, provider_config)
    response = PrivacyBroker(provider_config).generate_grounded_response(
        context.messages,
        attributes=context.attributes,
    ).content
    assert isinstance(response, str)
    record_query_result(session, context, response)

    return response
