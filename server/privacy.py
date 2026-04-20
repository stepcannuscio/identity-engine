"""Helpers for frontend-safe privacy state summaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from config.llm_router import ProviderConfig
from engine.privacy_broker import InferenceDecision
from server.models.schemas import PrivacyState

_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "groq": "Groq",
    "ollama": "Local model",
}


def _provider_label(provider: str | None, *, is_local: bool) -> str | None:
    if is_local:
        return "Local model"
    if not provider:
        return None
    return _PROVIDER_LABELS.get(provider.lower(), provider.title())


def _local_summary(routing_enforced: bool, contains_local_only_context: bool) -> str:
    if routing_enforced and contains_local_only_context:
        return "Processed locally so local-only data stayed on this device."
    if routing_enforced:
        return "Processed locally with privacy rules applied."
    return "Processed locally."


def _external_summary(routing_enforced: bool) -> str:
    if routing_enforced:
        return "Used an external model after privacy rules were applied."
    return "Used an external model."


def blocked_privacy_state(
    provider_config: ProviderConfig | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    routing_enforced: bool = True,
) -> PrivacyState:
    """Return a safe blocked-state summary."""
    resolved_provider = provider
    resolved_model = model
    is_local = False
    if provider_config is not None:
        resolved_provider = provider_config.provider
        resolved_model = provider_config.model
        is_local = provider_config.is_local

    return PrivacyState(
        execution_mode="blocked",
        routing_enforced=routing_enforced,
        warning_present=True,
        used_local_fallback=False,
        provider_label=_provider_label(resolved_provider, is_local=is_local),
        model_label=None if is_local else resolved_model,
        summary="Blocked to protect local-only data from being sent externally.",
    )


def unavailable_privacy_state(provider_config: ProviderConfig) -> PrivacyState:
    """Return a safe fallback when execution details are unavailable."""
    execution_mode: Literal["local", "external"] = (
        "local" if provider_config.is_local else "external"
    )
    return PrivacyState(
        execution_mode=execution_mode,
        routing_enforced=True,
        warning_present=True,
        used_local_fallback=False,
        provider_label=_provider_label(provider_config.provider, is_local=provider_config.is_local),
        model_label=provider_config.model,
        summary="Privacy status is unavailable because the request did not complete.",
    )


def privacy_state_from_decision(decision: InferenceDecision) -> PrivacyState:
    """Normalize broker audit metadata into a UI-safe privacy summary."""
    if decision.decision == "blocked":
        return blocked_privacy_state(
            provider=decision.provider,
            model=decision.model,
            routing_enforced=decision.routing_enforced,
        )

    execution_mode: Literal["local", "external"] = (
        "local" if decision.is_local else "external"
    )
    used_local_fallback = decision.reason == "used_local_artifact_fallback"
    summary = (
        (
            "Processed locally using a privacy-preserving fallback because the best evidence lived in local uploads."
            if used_local_fallback
            else _local_summary(decision.routing_enforced, decision.contains_local_only_context)
        )
        if decision.is_local
        else _external_summary(decision.routing_enforced)
    )
    return PrivacyState(
        execution_mode=execution_mode,
        routing_enforced=decision.routing_enforced,
        warning_present=bool(decision.warning),
        used_local_fallback=used_local_fallback,
        provider_label=_provider_label(decision.provider, is_local=decision.is_local),
        model_label=decision.model,
        summary=summary,
    )


def privacy_state_from_provider(provider_config: ProviderConfig) -> PrivacyState:
    """Return the best available privacy summary before inference completes."""
    execution_mode: Literal["local", "external"] = (
        "local" if provider_config.is_local else "external"
    )
    summary = (
        "Processing locally with privacy rules applied."
        if provider_config.is_local
        else "Preparing an external request with privacy rules applied."
    )
    return PrivacyState(
        execution_mode=execution_mode,
        routing_enforced=True,
        warning_present=False,
        used_local_fallback=False,
        provider_label=_provider_label(provider_config.provider, is_local=provider_config.is_local),
        model_label=provider_config.model,
        summary=summary,
    )


def privacy_state_from_routing_log(entry: Mapping[str, Any]) -> PrivacyState:
    """Normalize one stored routing-log entry for safe frontend display."""
    decision = str(entry.get("decision", "allowed") or "allowed")
    routing_enforced = bool(entry.get("routing_enforced"))
    is_local = entry.get("is_local")

    if decision == "blocked":
        return blocked_privacy_state(
            provider=str(entry.get("provider")) if entry.get("provider") else None,
            model=str(entry.get("model")) if entry.get("model") else None,
            routing_enforced=routing_enforced or True,
        )

    execution_mode: Literal["local", "external", "unknown"]
    if is_local is True:
        execution_mode = "local"
        summary = _local_summary(
            routing_enforced,
            bool(entry.get("contains_local_only_context")),
        )
    elif is_local is False:
        execution_mode = "external"
        summary = _external_summary(routing_enforced)
    else:
        backend = str(entry.get("backend", "")).lower()
        if backend == "local":
            execution_mode = "local"
            summary = _local_summary(routing_enforced, False)
        elif backend:
            execution_mode = "external"
            summary = _external_summary(routing_enforced)
        else:
            execution_mode = "unknown"
            summary = "Privacy state unavailable."

    return PrivacyState(
        execution_mode=execution_mode,
        routing_enforced=routing_enforced,
        warning_present=bool(entry.get("warning")),
        used_local_fallback=str(entry.get("reason", "")) == "used_local_artifact_fallback",
        provider_label=_provider_label(
            str(entry.get("provider")) if entry.get("provider") else None,
            is_local=execution_mode == "local",
        ),
        model_label=str(entry.get("model")) if entry.get("model") else None,
        summary=summary,
    )


def session_privacy_state(entries: list[Mapping[str, Any]]) -> PrivacyState:
    """Summarize the privacy posture of one stored session."""
    if not entries:
        return PrivacyState(
            execution_mode="unknown",
            routing_enforced=False,
            warning_present=False,
            used_local_fallback=False,
            provider_label=None,
            model_label=None,
            summary="No privacy activity was recorded for this session.",
        )

    privacy_entries = [privacy_state_from_routing_log(entry) for entry in entries]
    if any(entry.execution_mode == "blocked" for entry in privacy_entries):
        return PrivacyState(
            execution_mode="blocked",
            routing_enforced=True,
            warning_present=True,
            used_local_fallback=False,
            provider_label=None,
            model_label=None,
            summary="This session included a blocked external attempt to protect local-only data.",
        )

    if any(entry.execution_mode == "external" for entry in privacy_entries):
        return PrivacyState(
            execution_mode="external",
            routing_enforced=any(entry.routing_enforced for entry in privacy_entries),
            warning_present=any(entry.warning_present for entry in privacy_entries),
            used_local_fallback=False,
            provider_label=None,
            model_label=None,
            summary="This session used an external model with privacy checks in place.",
        )

    return PrivacyState(
        execution_mode="local",
        routing_enforced=any(entry.routing_enforced for entry in privacy_entries),
        warning_present=any(entry.warning_present for entry in privacy_entries),
        used_local_fallback=any(entry.used_local_fallback for entry in privacy_entries),
        provider_label=None,
        model_label=None,
        summary="This session stayed local.",
    )
