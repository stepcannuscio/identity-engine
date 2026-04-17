"""Public query-engine interface.

This module ties together classification, retrieval, prompt assembly, and LLM
response generation, while keeping all session state in the Session object.
It also runs the deterministic coverage evaluator after context assembly so
the pipeline can either hedge or skip LLM inference when ground-truth is thin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from engine.acquisition_planner import AcquisitionPlan, build_acquisition_plan
from engine.context_assembler import AssembledContext, assemble_query_context
from engine.coverage_evaluator import (
    INSUFFICIENT_DATA_MESSAGE,
    CoverageAssessment,
    evaluate_coverage,
)
from engine.privacy_broker import InferenceDecision, PrivacyBroker
from engine.prompt_builder import RoutingViolationError, build_prompt
from engine.query_classifier import build_query_plan
from engine.session import Session


@dataclass
class QueryContext:
    """Prepared query state used by both CLI and API entrypoints."""

    query: str
    query_type: str
    source_profile: str
    assembled_context: AssembledContext
    attributes: list[dict]
    messages: list[dict[str, str]]
    backend: str
    coverage: CoverageAssessment
    acquisition: AcquisitionPlan


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
    query_plan = build_query_plan(user_query)
    assembled_context = assemble_query_context(
        user_query,
        query_plan.retrieval_mode,
        query_plan.source_profile,
        session.get_history(),
        conn,
    )

    backend = "local" if getattr(provider_config, "is_local", False) else "external"
    preference_attributes = _preference_attributes_for_backend(assembled_context, backend)
    coverage = evaluate_coverage(assembled_context, backend=backend)
    acquisition = build_acquisition_plan(user_query, assembled_context, coverage)

    messages = build_prompt(
        assembled_context,
        target_backend=backend,
        enforce_routing=False,
        confidence=coverage.confidence,
    )

    return QueryContext(
        query=user_query,
        query_type=query_plan.retrieval_mode,
        source_profile=query_plan.source_profile,
        assembled_context=assembled_context,
        attributes=assembled_context.attributes + preference_attributes,
        messages=messages,
        backend=backend,
        coverage=coverage,
        acquisition=acquisition,
    )


def _privacy_would_block(context: QueryContext) -> bool:
    """Return True when the broker would reject the request on routing grounds.

    The short-circuit for ``insufficient_data`` must defer in that case so the
    privacy guardrail still produces the correct blocked audit.
    """
    if context.backend == "local":
        return False
    return bool(context.assembled_context.contains_local_only)


def build_insufficient_data_decision(
    context: QueryContext,
    provider_config,
) -> InferenceDecision:
    """Build a synthetic audit decision for the short-circuited case."""
    return InferenceDecision(
        provider=provider_config.provider,
        model=provider_config.model,
        is_local=bool(provider_config.is_local),
        task_type="query_generation",
        blocked_external_attributes_count=0,
        routing_enforced=True,
        attribute_count=len(context.attributes),
        domains_used=context.assembled_context.domains_used,
        retrieval_mode=context.query_type,
        contains_local_only_context=context.assembled_context.contains_local_only,
        decision="skipped_insufficient_data",
        reason="coverage_evaluator_reported_insufficient_data",
        timestamp=datetime.now(UTC).isoformat(),
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

    if context.coverage.confidence == "insufficient_data" and not _privacy_would_block(
        context
    ):
        audit = build_insufficient_data_decision(context, provider_config)
        record_query_result(session, context, INSUFFICIENT_DATA_MESSAGE, audit)
        return INSUFFICIENT_DATA_MESSAGE

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
