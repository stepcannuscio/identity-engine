"""Helpers for onboarding/profile state and provider readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from config.llm_router import TIER_MODELS, _ollama_has_model, _ollama_is_running, detect_hardware
from config.settings import has_api_key

PROFILE_CODES = (
    "private_local_first",
    "balanced_hybrid",
    "external_assist",
)


@dataclass(frozen=True)
class ProviderStatus:
    """One provider availability snapshot."""

    provider: str
    label: str
    configured: bool
    available: bool
    validated: bool
    is_local: bool
    model: str | None = None
    reason: str | None = None


def get_app_settings(conn) -> dict[str, object]:
    """Return the single-row app settings record."""
    row = conn.execute(
        """
        SELECT onboarding_completed, active_profile, preferred_backend
        FROM app_settings
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT OR IGNORE INTO app_settings (id, onboarding_completed, active_profile, preferred_backend)
            VALUES (1, 0, NULL, 'local')
            """
        )
        conn.commit()
        return {
            "onboarding_completed": False,
            "active_profile": None,
            "preferred_backend": "local",
        }
    return {
        "onboarding_completed": bool(row[0]),
        "active_profile": str(row[1]) if row[1] else None,
        "preferred_backend": str(row[2]),
    }


def update_app_settings(
    conn,
    *,
    onboarding_completed: bool | None = None,
    active_profile: str | None = None,
    preferred_backend: str | None = None,
) -> dict[str, object]:
    """Update the app settings row and return the latest values."""
    current = get_app_settings(conn)
    next_onboarding = current["onboarding_completed"] if onboarding_completed is None else onboarding_completed
    next_profile = current["active_profile"] if active_profile is None else active_profile
    next_backend = current["preferred_backend"] if preferred_backend is None else preferred_backend
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO app_settings (id, onboarding_completed, active_profile, preferred_backend, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            onboarding_completed = excluded.onboarding_completed,
            active_profile = excluded.active_profile,
            preferred_backend = excluded.preferred_backend,
            updated_at = excluded.updated_at
        """,
        (
            1,
            1 if next_onboarding else 0,
            next_profile,
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

    statuses = [
        ProviderStatus(
            provider="ollama",
            label="Local model",
            configured=bool(local_model),
            available=ollama_ready,
            validated=ollama_ready,
            is_local=True,
            model=local_model,
            reason=None if local_model else "This machine is below the current local tier.",
        ),
        ProviderStatus(
            provider="anthropic",
            label="Anthropic",
            configured=has_api_key("anthropic"),
            available=has_api_key("anthropic"),
            validated=has_api_key("anthropic"),
            is_local=False,
            model="claude-sonnet-4-6",
            reason=None if has_api_key("anthropic") else "API key not configured.",
        ),
        ProviderStatus(
            provider="groq",
            label="Groq",
            configured=has_api_key("groq"),
            available=has_api_key("groq"),
            validated=has_api_key("groq"),
            is_local=False,
            model="llama-3.1-8b-instant",
            reason=None if has_api_key("groq") else "API key not configured.",
        ),
    ]

    now = datetime.now(UTC).isoformat()
    conn.executemany(
        """
        INSERT INTO provider_status (provider, configured, validated, last_validated_at, last_error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            configured = excluded.configured,
            validated = excluded.validated,
            last_validated_at = excluded.last_validated_at,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        [
            (
                status.provider,
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


def build_recommended_profiles(statuses: list[ProviderStatus]) -> list[dict[str, object]]:
    """Return available privacy/model profiles with one recommendation."""
    status_map = {status.provider: status for status in statuses}
    local_ready = bool(status_map.get("ollama", ProviderStatus("ollama", "Local model", False, False, False, True)).available)
    external_ready = any(
        status_map.get(name, ProviderStatus(name, name.title(), False, False, False, False)).available
        for name in ("anthropic", "groq")
    )

    profiles = [
        {
            "code": "private_local_first",
            "label": "Private local first",
            "description": "Prefer local models whenever possible and keep external use opt-in.",
            "default_backend": "local",
            "requires_external_provider": False,
            "available": local_ready,
            "recommended": False,
        },
        {
            "code": "balanced_hybrid",
            "label": "Balanced hybrid",
            "description": "Use local by default with an external fallback when explicitly enabled.",
            "default_backend": "local",
            "requires_external_provider": True,
            "available": local_ready and external_ready,
            "recommended": False,
        },
        {
            "code": "external_assist",
            "label": "External assist",
            "description": "Use an external provider for broader assistance when privacy settings allow it.",
            "default_backend": "external",
            "requires_external_provider": True,
            "available": external_ready,
            "recommended": False,
        },
    ]

    recommended_code = "private_local_first"
    if local_ready and external_ready:
        recommended_code = "balanced_hybrid"
    elif external_ready and not local_ready:
        recommended_code = "external_assist"

    for profile in profiles:
        profile["recommended"] = profile["code"] == recommended_code
    return profiles


def resolve_profile_backend(profile_code: str, statuses: list[ProviderStatus]) -> str:
    """Map a chosen profile to a usable default backend."""
    status_map = {status.provider: status for status in statuses}
    external_ready = any(
        status_map.get(name, ProviderStatus(name, name.title(), False, False, False, False)).available
        for name in ("anthropic", "groq")
    )
    local_ready = bool(
        status_map.get("ollama", ProviderStatus("ollama", "Local model", False, False, False, True)).configured
    )

    if profile_code == "external_assist" and external_ready:
        return "external"
    if profile_code in {"private_local_first", "balanced_hybrid"} and local_ready:
        return "local"
    if external_ready:
        return "external"
    return "local"
