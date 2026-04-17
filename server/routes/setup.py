"""Onboarding/setup routes for provider configuration and security posture."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from config.settings import set_api_key
from engine.security_posture import inspect_security_posture
from engine.setup_state import (
    build_recommended_profiles,
    get_app_settings,
    get_provider_statuses,
    resolve_profile_backend,
    update_app_settings,
)
from server.db import get_db_connection
from server.models.schemas import (
    PrivacyProfileOption,
    ProviderCredentialRequest,
    ProviderStatusResponse,
    SecurityCheckResponse,
    SecurityPostureResponse,
    SetupOptionsResponse,
    SetupProfileRequest,
)

router = APIRouter(tags=["setup"])
_SUPPORTED_PROVIDERS = {"anthropic", "groq"}


def _provider_statuses(conn) -> list[ProviderStatusResponse]:
    return [
        ProviderStatusResponse(
            provider=status.provider,  # type: ignore[arg-type]
            label=status.label,
            configured=status.configured,
            available=status.available,
            validated=status.validated,
            is_local=status.is_local,
            model=status.model,
            reason=status.reason,
        )
        for status in get_provider_statuses(conn)
    ]


def _profile_option(profile: dict[str, object]) -> PrivacyProfileOption:
    return PrivacyProfileOption(
        code=cast(Any, str(profile["code"])),
        label=str(profile["label"]),
        description=str(profile["description"]),
        default_backend=cast(Any, str(profile["default_backend"])),
        requires_external_provider=bool(profile["requires_external_provider"]),
        available=bool(profile["available"]),
        recommended=bool(profile["recommended"]),
    )


def _validate_api_key(provider: str, api_key: str) -> None:
    value = api_key.strip()
    if len(value) < 12:
        raise HTTPException(status_code=422, detail="api key is too short")
    if provider == "anthropic" and not value.startswith("sk-ant-"):
        raise HTTPException(status_code=422, detail="anthropic keys should start with sk-ant-")
    if provider == "groq" and not value.startswith("gsk_"):
        raise HTTPException(status_code=422, detail="groq keys should start with gsk_")


@router.get("/setup/model-options", response_model=SetupOptionsResponse)
def model_options(request: Request) -> SetupOptionsResponse:
    """Return provider availability and recommended privacy profiles."""
    _ = request
    with get_db_connection() as conn:
        settings = get_app_settings(conn)
        statuses = get_provider_statuses(conn)
        provider_statuses = [
            ProviderStatusResponse(
                provider=status.provider,  # type: ignore[arg-type]
                label=status.label,
                configured=status.configured,
                available=status.available,
                validated=status.validated,
                is_local=status.is_local,
                model=status.model,
                reason=status.reason,
            )
            for status in statuses
        ]
        profiles = [_profile_option(profile) for profile in build_recommended_profiles(statuses)]
    return SetupOptionsResponse(
        providers=provider_statuses,
        profiles=profiles,
        active_profile=cast(str | None, settings["active_profile"]),
        preferred_backend=cast(Any, settings["preferred_backend"]),
    )


@router.post("/setup/providers/{provider}/credentials", response_model=ProviderStatusResponse)
def save_provider_credentials(
    provider: str,
    payload: ProviderCredentialRequest,
    request: Request,
) -> ProviderStatusResponse:
    """Store an external-provider API key in the system keychain."""
    _ = request
    provider = provider.lower()
    if provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="unsupported provider")
    _validate_api_key(provider, payload.api_key)
    set_api_key(provider, payload.api_key.strip())
    with get_db_connection() as conn:
        statuses = {status.provider: status for status in get_provider_statuses(conn)}
        status = statuses[provider]
    return ProviderStatusResponse(
        provider=status.provider,  # type: ignore[arg-type]
        label=status.label,
        configured=status.configured,
        available=status.available,
        validated=status.validated,
        is_local=status.is_local,
        model=status.model,
        reason=status.reason,
    )


@router.post("/setup/profile", response_model=SetupOptionsResponse)
def save_profile(payload: SetupProfileRequest, request: Request) -> SetupOptionsResponse:
    """Persist the chosen onboarding profile and preferred backend."""
    _ = request
    with get_db_connection() as conn:
        statuses = get_provider_statuses(conn)
        preferred_backend = payload.preferred_backend or resolve_profile_backend(payload.profile, statuses)
        update_app_settings(
            conn,
            active_profile=payload.profile,
            preferred_backend=preferred_backend,
            onboarding_completed=(
                payload.onboarding_completed
                if payload.onboarding_completed is not None
                else cast(bool, get_app_settings(conn)["onboarding_completed"])
            ),
        )
        settings = get_app_settings(conn)
        provider_statuses = [
            ProviderStatusResponse(
                provider=status.provider,  # type: ignore[arg-type]
                label=status.label,
                configured=status.configured,
                available=status.available,
                validated=status.validated,
                is_local=status.is_local,
                model=status.model,
                reason=status.reason,
            )
            for status in statuses
        ]
        profiles = [_profile_option(profile) for profile in build_recommended_profiles(statuses)]
    return SetupOptionsResponse(
        providers=provider_statuses,
        profiles=profiles,
        active_profile=cast(str | None, settings["active_profile"]),
        preferred_backend=cast(Any, settings["preferred_backend"]),
    )


@router.get("/setup/security-posture", response_model=SecurityPostureResponse)
def security_posture(request: Request) -> SecurityPostureResponse:
    """Return read-only machine security recommendations."""
    _ = request
    posture = inspect_security_posture()
    return SecurityPostureResponse(
        platform=str(posture["platform"]),
        supported=bool(posture["supported"]),
        checks=[SecurityCheckResponse(**check) for check in posture["checks"]],  # type: ignore[arg-type]
    )
