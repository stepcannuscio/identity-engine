"""Public query-engine interface.

This module ties together classification, retrieval, prompt assembly, and LLM
response generation, while keeping all session state in the Session object.
It also runs the deterministic coverage evaluator after context assembly so
the pipeline can either hedge or skip LLM inference when ground-truth is thin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace as dc_replace
from datetime import UTC, datetime
from typing import Any

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
from engine.session_learner import maybe_extract_from_exchange
from engine.setup_state import resolve_local_provider_config

logger = logging.getLogger(__name__)


@dataclass
class QueryContext:
    """Prepared query state used by both CLI and API entrypoints."""

    query: str
    query_type: str
    source_profile: str
    intent_tags: list[str]
    domain_hints: list[str]
    classification_reason: str
    assembled_context: AssembledContext
    attributes: list[dict]
    messages: list[dict[str, str]]
    backend: str
    requested_backend: str
    provider_config: Any
    local_fallback_used: bool
    forced_response: str | None
    coverage: CoverageAssessment
    acquisition: AcquisitionPlan


def _backend_label(provider_config: Any) -> str:
    """Return a backend label string for attribute filtering and audit logging."""
    if getattr(provider_config, "is_local", False):
        return "local"
    if getattr(provider_config, "provider", None) == "private_server":
        return "private_server"
    return "external"


def _preference_attributes_for_backend(
    assembled_context: AssembledContext,
    backend: str,
) -> list[dict]:
    if backend in ("local", "private_server"):
        return assembled_context.preference_attributes
    return [
        attribute
        for attribute in assembled_context.preference_attributes
        if attribute.get("routing") != "local_only"
    ]


def _identity_attributes_for_backend(
    assembled_context: AssembledContext,
    backend: str,
) -> list[dict]:
    if backend in ("local", "private_server"):
        return assembled_context.attributes
    return [
        attribute
        for attribute in assembled_context.attributes
        if attribute.get("routing") != "local_only"
    ]


def _should_reroute_to_artifact_grounded_self(
    source_profile: str,
    assembled_context: AssembledContext,
) -> bool:
    return (
        source_profile == "self_question"
        and not assembled_context.attributes
        and not assembled_context.preference_attributes
        and bool(assembled_context.artifact_chunks)
    )


def _artifact_only_external_response() -> str:
    return (
        "I found relevant evidence in your local uploaded artifacts, but I can't send that "
        "local-only content to the current external model. Enable a local model to answer "
        "from those uploads directly."
    )


def _build_query_context(
    user_query: str,
    session: Session,
    conn,
    *,
    retrieval_mode: str,
    source_profile: str,
    intent_tags: list[str],
    domain_hints: list[str],
    provider_config,
    requested_backend: str,
    local_fallback_used: bool,
    forced_response: str | None,
) -> QueryContext:
    assembled_context = assemble_query_context(
        user_query,
        retrieval_mode,
        source_profile,
        session.get_history(),
        conn,
        intent_tags=intent_tags,
        domain_hints=domain_hints,
        provider_config=provider_config,
    )
    backend = _backend_label(provider_config)
    identity_attributes = _identity_attributes_for_backend(assembled_context, backend)
    preference_attributes = _preference_attributes_for_backend(assembled_context, backend)
    if backend == "external" and (
        len(identity_attributes) < len(assembled_context.attributes)
        or len(preference_attributes) < len(assembled_context.preference_attributes)
        or any(chunk.get("routing") == "local_only" for chunk in assembled_context.artifact_chunks)
    ):
        assembled_context = dc_replace(assembled_context, had_local_only_stripped=True)
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
        query_type=retrieval_mode,
        source_profile=source_profile,
        intent_tags=intent_tags,
        domain_hints=domain_hints,
        classification_reason="",
        assembled_context=assembled_context,
        attributes=identity_attributes + preference_attributes,
        messages=messages,
        backend=backend,
        requested_backend=requested_backend,
        provider_config=provider_config,
        local_fallback_used=local_fallback_used,
        forced_response=forced_response,
        coverage=coverage,
        acquisition=acquisition,
    )


def prepare_query(
    user_query: str,
    session: Session,
    conn,
    provider_config,
) -> QueryContext:
    """Prepare a query without generating a response yet."""
    query_plan = build_query_plan(user_query)
    requested_backend = _backend_label(provider_config)

    context = _build_query_context(
        user_query,
        session,
        conn,
        retrieval_mode=query_plan.retrieval_mode,
        source_profile=query_plan.source_profile,
        intent_tags=query_plan.intent_tags,
        domain_hints=query_plan.domain_hints,
        provider_config=provider_config,
        requested_backend=requested_backend,
        local_fallback_used=False,
        forced_response=None,
    )
    context.classification_reason = query_plan.classification_reason

    if _should_reroute_to_artifact_grounded_self(query_plan.source_profile, context.assembled_context):
        context = _build_query_context(
            user_query,
            session,
            conn,
            retrieval_mode=query_plan.retrieval_mode,
            source_profile="artifact_grounded_self",
            intent_tags=query_plan.intent_tags,
            domain_hints=query_plan.domain_hints,
            provider_config=provider_config,
            requested_backend=requested_backend,
            local_fallback_used=False,
            forced_response=None,
        )
        context.classification_reason = (
            "rerouted self-style query to artifact-grounded answering because only uploaded evidence was available"
        )

    if context.source_profile == "artifact_grounded_self" and requested_backend not in ("local", "private_server"):
        try:
            local_provider_config = resolve_local_provider_config(provider_config)
        except Exception:
            context.forced_response = _artifact_only_external_response()
            return context

        context = _build_query_context(
            user_query,
            session,
            conn,
            retrieval_mode=context.query_type,
            source_profile=context.source_profile,
            intent_tags=context.intent_tags,
            domain_hints=context.domain_hints,
            provider_config=local_provider_config,
            requested_backend=requested_backend,
            local_fallback_used=True,
            forced_response=None,
        )
        context.classification_reason = (
            "used local artifact fallback because the best evidence was stored in local uploads"
        )

    return context


def _privacy_would_block(context: QueryContext) -> bool:
    """Return True when the broker would reject the request on routing grounds.

    The short-circuit for ``insufficient_data`` must defer in that case so the
    privacy guardrail still produces the correct blocked audit.
    """
    if context.backend == "local" or context.forced_response is not None:
        return False
    return any(attr.get("routing") == "local_only" for attr in context.attributes)


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
        local_only_stripped_for_external=context.assembled_context.had_local_only_stripped,
        decision="skipped_insufficient_data",
        reason="coverage_evaluator_reported_insufficient_data",
        timestamp=datetime.now(UTC).isoformat(),
    )


def build_forced_artifact_response_decision(
    context: QueryContext,
    provider_config,
) -> InferenceDecision:
    """Build a synthetic audit decision for artifact-aware fallback messages."""
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
        decision="skipped_local_fallback_unavailable",
        reason="local_only_artifact_evidence_requires_local_model",
        warning="Relevant uploaded artifact evidence was kept local.",
        timestamp=datetime.now(UTC).isoformat(),
    )


def apply_local_fallback_audit(
    audit: InferenceDecision,
    context: QueryContext,
) -> InferenceDecision:
    """Annotate a successful local-fallback response for UI and logging."""
    if not getattr(context, "local_fallback_used", False):
        return audit
    return dc_replace(
        audit,
        warning="Used a local-only fallback because uploaded artifact evidence was local.",
        reason="used_local_artifact_fallback",
    )


def record_query_result(
    conn,
    session: Session,
    context: QueryContext,
    response: str,
    audit: InferenceDecision,
) -> None:
    """Persist in-memory session metadata after a completed query."""
    session.add_exchange(context.query, response)
    session.query_count += 1
    session.attributes_retrieved += len(context.attributes)
    session.log_query(audit, query_type=context.query_type)
    try:
        maybe_extract_from_exchange(
            conn,
            session,
            user_query=context.query,
            coverage_confidence=context.coverage.confidence,
            retrieved_attributes=context.attributes,
            provider_config=context.provider_config,
            source_profile=context.source_profile,
            domain_hints=context.domain_hints,
        )
    except Exception:
        logger.exception("Passive session learning failed after query completion.")


def record_blocked_query(session: Session, context: QueryContext, error: Exception) -> None:
    """Persist privacy-blocked broker decisions without changing query behavior."""
    audit = getattr(error, "audit", None)
    if isinstance(audit, InferenceDecision):
        session.log_query(audit, query_type=context.query_type)


def query(
    user_query: str,
    session: Session,
    conn,
    provider_config,
) -> str:
    """Run one end-to-end query and update only in-memory session state."""
    context = prepare_query(user_query, session, conn, provider_config)

    if context.forced_response is not None:
        audit = build_forced_artifact_response_decision(context, provider_config)
        record_query_result(conn, session, context, context.forced_response, audit)
        return context.forced_response

    if context.coverage.confidence == "insufficient_data" and not _privacy_would_block(
        context
    ):
        audit = build_insufficient_data_decision(context, context.provider_config)
        record_query_result(conn, session, context, INSUFFICIENT_DATA_MESSAGE, audit)
        return INSUFFICIENT_DATA_MESSAGE

    try:
        result = PrivacyBroker(context.provider_config).generate_grounded_response(
            context.messages,
            attributes=context.attributes,
            retrieval_mode=context.query_type,
            contains_local_only_context=context.assembled_context.contains_local_only,
            local_only_stripped_for_external=context.assembled_context.had_local_only_stripped,
            domains_used=context.assembled_context.domains_used,
        )
    except RoutingViolationError as exc:
        record_blocked_query(session, context, exc)
        raise
    response = result.content
    assert isinstance(response, str)
    record_query_result(
        conn,
        session,
        context,
        response,
        apply_local_fallback_audit(result.metadata, context),
    )

    return response
