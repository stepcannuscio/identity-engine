"""Helpers for onboarding privacy preferences, provider readiness, and runtime selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from config.llm_router import (
    ProviderConfig,
    TIER_MODELS,
    _ollama_has_model,
    _ollama_is_running,
    detect_hardware,
    resolve_external_router,
    resolve_provider_router,
)
from config.provider_catalog import (
    CredentialField,
    get_provider_definition,
    list_external_provider_ids,
    list_provider_definitions,
)
from config.settings import has_api_key

PROFILE_CODES = (
    "private_local_first",
    "balanced_hybrid",
    "external_assist",
)
PRIVACY_PREFERENCE_CODES = (
    "privacy_first",
    "balanced",
    "capability_first",
)


@dataclass(frozen=True)
class ProviderStatus:
    """One provider availability snapshot."""

    provider: str
    label: str
    deployment: str
    trust_boundary: str
    auth_strategy: str
    configured: bool
    available: bool
    validated: bool
    is_local: bool
    description: str | None = None
    setup_hint: str | None = None
    credential_fields: tuple[CredentialField, ...] = field(default_factory=tuple)
    model: str | None = None
    reason: str | None = None


def build_privacy_preferences() -> list[dict[str, str]]:
    """Return user-facing privacy preference options for onboarding."""
    return [
        {
            "code": "privacy_first",
            "label": "Privacy first",
            "description": "Favor self-hosted execution and recommend external use only when needed.",
        },
        {
            "code": "balanced",
            "label": "Balanced",
            "description": "Blend self-hosted and external options based on what your machine can support.",
        },
        {
            "code": "capability_first",
            "label": "Capability first",
            "description": "Prefer broader hosted model access when privacy rules allow it.",
        },
    ]


def get_app_settings(conn) -> dict[str, object]:
    """Return the single-row app settings record."""
    row = conn.execute(
        """
        SELECT
            onboarding_completed,
            privacy_preference,
            active_profile,
            preferred_provider,
            preferred_backend
        FROM app_settings
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT OR IGNORE INTO app_settings (
                id,
                onboarding_completed,
                privacy_preference,
                active_profile,
                preferred_provider,
                preferred_backend
            )
            VALUES (1, 0, NULL, NULL, NULL, 'local')
            """
        )
        conn.commit()
        return {
            "onboarding_completed": False,
            "privacy_preference": None,
            "active_profile": None,
            "preferred_provider": None,
            "preferred_backend": "local",
        }
    return {
        "onboarding_completed": bool(row[0]),
        "privacy_preference": str(row[1]) if row[1] else None,
        "active_profile": str(row[2]) if row[2] else None,
        "preferred_provider": str(row[3]) if row[3] else None,
        "preferred_backend": str(row[4]),
    }


def update_app_settings(
    conn,
    *,
    onboarding_completed: bool | None = None,
    privacy_preference: str | None = None,
    active_profile: str | None = None,
    preferred_provider: str | None = None,
    preferred_backend: str | None = None,
) -> dict[str, object]:
    """Update the app settings row and return the latest values."""
    current = get_app_settings(conn)
    next_onboarding = current["onboarding_completed"] if onboarding_completed is None else onboarding_completed
    next_privacy_preference = (
        current["privacy_preference"] if privacy_preference is None else privacy_preference
    )
    next_profile = current["active_profile"] if active_profile is None else active_profile
    next_provider = current["preferred_provider"] if preferred_provider is None else preferred_provider
    next_backend = current["preferred_backend"] if preferred_backend is None else preferred_backend
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO app_settings (
            id,
            onboarding_completed,
            privacy_preference,
            active_profile,
            preferred_provider,
            preferred_backend,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            onboarding_completed = excluded.onboarding_completed,
            privacy_preference = excluded.privacy_preference,
            active_profile = excluded.active_profile,
            preferred_provider = excluded.preferred_provider,
            preferred_backend = excluded.preferred_backend,
            updated_at = excluded.updated_at
        """,
        (
            1,
            1 if next_onboarding else 0,
            next_privacy_preference,
            next_profile,
            next_provider,
            next_backend,
            now,
        ),
    )
    conn.commit()
    return get_app_settings(conn)


def get_provider_statuses(conn) -> list[ProviderStatus]:
    """Return current provider readiness and sync it into provider_status."""
    hardware = detect_hardware()
    local_tier = hardware["recommended_tier"]
    local_model = TIER_MODELS.get(local_tier)
    ollama_running = _ollama_is_running() if local_model else False
    ollama_ready = bool(local_model and ollama_running and _ollama_has_model(local_model))

    statuses: list[ProviderStatus] = []
    for definition in list_provider_definitions():
        if definition.provider == "ollama":
            statuses.append(
                ProviderStatus(
                    provider=definition.provider,
                    label=definition.label,
                    deployment=definition.deployment,
                    trust_boundary=definition.trust_boundary,
                    auth_strategy=definition.auth_strategy,
                    configured=bool(local_model),
                    available=ollama_ready,
                    validated=ollama_ready,
                    is_local=True,
                    description=definition.description,
                    setup_hint=definition.setup_hint,
                    credential_fields=definition.credential_fields,
                    model=local_model,
                    reason=(
                        None
                        if local_model and ollama_ready
                        else "Ollama is not ready on this machine."
                        if local_model
                        else "This machine does not currently meet the local model recommendation."
                    ),
                )
            )
            continue

        configured = has_api_key(definition.provider)
        statuses.append(
            ProviderStatus(
                provider=definition.provider,
                label=definition.label,
                deployment=definition.deployment,
                trust_boundary=definition.trust_boundary,
                auth_strategy=definition.auth_strategy,
                configured=configured,
                available=configured,
                validated=configured,
                is_local=False,
                description=definition.description,
                setup_hint=definition.setup_hint,
                credential_fields=definition.credential_fields,
                model=definition.default_model,
                reason=None if configured else "Credentials not configured yet.",
            )
        )

    now = datetime.now(UTC).isoformat()
    conn.executemany(
        """
        INSERT INTO provider_status (
            provider,
            deployment,
            trust_boundary,
            auth_strategy,
            configured,
            validated,
            last_validated_at,
            last_error,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            deployment = excluded.deployment,
            trust_boundary = excluded.trust_boundary,
            auth_strategy = excluded.auth_strategy,
            configured = excluded.configured,
            validated = excluded.validated,
            last_validated_at = excluded.last_validated_at,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        [
            (
                status.provider,
                status.deployment,
                status.trust_boundary,
                status.auth_strategy,
                1 if status.configured else 0,
                1 if status.validated else 0,
                now,
                status.reason,
                now,
            )
            for status in statuses
        ],
    )
    conn.commit()
    return statuses


def _available_external_providers(statuses: list[ProviderStatus]) -> list[str]:
    available = {
        status.provider
        for status in statuses
        if getattr(status, "deployment", "local" if status.is_local else "external") == "external"
        and status.available
    }
    return [provider for provider in list_external_provider_ids() if provider in available]


def _preferred_profile_code(
    privacy_preference: str | None,
    *,
    local_ready: bool,
    external_ready: bool,
) -> str:
    if privacy_preference == "privacy_first":
        if local_ready:
            return "private_local_first"
        if external_ready:
            return "external_assist"
        return "private_local_first"
    if privacy_preference == "capability_first":
        if external_ready:
            return "external_assist"
        if local_ready:
            return "private_local_first"
        return "external_assist"
    if local_ready and external_ready:
        return "balanced_hybrid"
    if local_ready:
        return "private_local_first"
    if external_ready:
        return "external_assist"
    return "private_local_first"


def build_recommended_profiles(
    statuses: list[ProviderStatus],
    privacy_preference: str | None = None,
) -> list[dict[str, object]]:
    """Return available privacy/model configurations with one recommendation."""
    available_external = _available_external_providers(statuses)
    best_external_provider = available_external[0] if available_external else None
    local_ready = any(
        getattr(status, "deployment", "local" if status.is_local else "external") == "local"
        and getattr(status, "trust_boundary", "self_hosted" if status.is_local else "external") == "self_hosted"
        and status.available
        for status in statuses
    )
    external_ready = bool(available_external)

    profiles = [
        {
            "code": "private_local_first",
            "label": "Private local first",
            "description": "Keep inference on self-hosted models by default and treat external access as optional.",
            "default_backend": "local",
            "provider_scope": "self_hosted_only",
            "provider_options": ["ollama"],
            "recommended_provider": "ollama" if local_ready else None,
            "recommendation_reason": (
                "Best fit when your priority is keeping identity reasoning on hardware you control."
            ),
            "requires_external_provider": False,
            "available": local_ready,
            "recommended": False,
        },
        {
            "code": "balanced_hybrid",
            "label": "Balanced hybrid",
            "description": "Use a self-hosted model first, with a configured external provider available when you explicitly switch.",
            "default_backend": "local",
            "provider_scope": "hybrid",
            "provider_options": list_external_provider_ids(),
            "recommended_provider": best_external_provider,
            "recommendation_reason": (
                "Best fit when your machine can handle local work and you want a hosted option ready for broader tasks."
            ),
            "requires_external_provider": True,
            "available": local_ready and external_ready,
            "recommended": False,
        },
        {
            "code": "external_assist",
            "label": "External assist",
            "description": "Use a configured external provider by default while privacy rules still block local-only data from leaving the device.",
            "default_backend": "external",
            "provider_scope": "external_default",
            "provider_options": list_external_provider_ids(),
            "recommended_provider": best_external_provider,
            "recommendation_reason": (
                "Best fit when you prefer managed model capabilities or your machine is not ready for local inference."
            ),
            "requires_external_provider": True,
            "available": external_ready,
            "recommended": False,
        },
    ]

    recommended_code = _preferred_profile_code(
        privacy_preference,
        local_ready=local_ready,
        external_ready=external_ready,
    )
    if not any(profile["code"] == recommended_code and profile["available"] for profile in profiles):
        fallback = next((profile["code"] for profile in profiles if profile["available"]), recommended_code)
        recommended_code = str(fallback)

    for profile in profiles:
        profile["recommended"] = profile["code"] == recommended_code
    return profiles


def resolve_profile_backend(profile_code: str, statuses: list[ProviderStatus]) -> str:
    """Map a chosen profile to a usable default backend."""
    external_ready = bool(_available_external_providers(statuses))
    local_ready = any(
        getattr(status, "deployment", "local" if status.is_local else "external") == "local"
        and getattr(status, "trust_boundary", "self_hosted" if status.is_local else "external") == "self_hosted"
        and status.available
        for status in statuses
    )

    if profile_code == "external_assist" and external_ready:
        return "external"
    if profile_code in {"private_local_first", "balanced_hybrid"} and local_ready:
        return "local"
    if external_ready:
        return "external"
    return "local"


def resolve_active_provider_config(
    conn,
    default_config: ProviderConfig,
    *,
    backend_override: str | None = None,
) -> ProviderConfig:
    """Resolve the provider config that matches persisted onboarding choices."""
    settings = get_app_settings(conn)
    preferred_backend = backend_override or str(settings["preferred_backend"])
    preferred_provider = str(settings["preferred_provider"]) if settings["preferred_provider"] else None

    if preferred_backend == "local":
        if default_config.is_local:
            return default_config
        return resolve_provider_router("ollama")

    if preferred_backend != "external":
        return default_config

    if preferred_provider:
        provider_definition = get_provider_definition(preferred_provider)
        if provider_definition.deployment == "external":
            if not default_config.is_local and default_config.provider == preferred_provider:
                return default_config
            return resolve_provider_router(preferred_provider)

    if not default_config.is_local:
        return default_config
    return resolve_external_router()
