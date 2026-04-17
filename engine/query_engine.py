"""Public query-engine interface.

This module ties together classification, retrieval, prompt assembly, and LLM
response generation, while keeping all session state in the Session object.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.context_assembler import AssembledContext, assemble_query_context
from engine.privacy_broker import InferenceDecision, PrivacyBroker
from engine.prompt_builder import RoutingViolationError, build_prompt
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


def _preference_attributes_for_backend(
    assembled_context: AssembledContext,
    backend: str,
) -> list[dict]:
    if backend == "local":
        return assembled_context.preference_attributes
    return [
        attribute
        for attribute in assembled_context.preference_attributes
        if attribute.get("routing") != "local_only"
    ]


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
    preference_attributes = _preference_attributes_for_backend(assembled_context, backend)

    messages = build_prompt(
        assembled_context,
        target_backend=backend,
        enforce_routing=False,
    )

    return QueryContext(
        query=user_query,
        query_type=query_type,
        assembled_context=assembled_context,
        attributes=assembled_context.attributes + preference_attributes,
        messages=messages,
        backend=backend,
    )


def record_query_result(
    session: Session,
    context: QueryContext,
    response: str,
    audit: InferenceDecision,
) -> None:
    """Persist in-memory session metadata after a completed query."""
    session.add_exchange(context.query, response)
    session.query_count += 1
    session.attributes_retrieved += len(context.attributes)
    session.log_query(context.query, audit, query_type=context.query_type)


def record_blocked_query(session: Session, context: QueryContext, error: Exception) -> None:
    """Persist privacy-blocked broker decisions without changing query behavior."""
    audit = getattr(error, "audit", None)
    if isinstance(audit, InferenceDecision):
        session.log_query(context.query, audit, query_type=context.query_type)


def query(
    user_query: str,
    session: Session,
    conn,
    provider_config,
) -> str:
    """Run one end-to-end query and update only in-memory session state."""
    context = prepare_query(user_query, session, conn, provider_config)
    try:
        result = PrivacyBroker(provider_config).generate_grounded_response(
            context.messages,
            attributes=context.attributes,
            retrieval_mode=context.query_type,
            contains_local_only_context=context.assembled_context.contains_local_only,
            domains_used=context.assembled_context.domains_used,
        )
    except RoutingViolationError as exc:
        record_blocked_query(session, context, exc)
        raise
    response = result.content
    assert isinstance(response, str)
    record_query_result(session, context, response, result.metadata)

    return response
