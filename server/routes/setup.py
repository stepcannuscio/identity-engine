"""Onboarding/setup routes for provider configuration and security posture."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from config.provider_catalog import get_provider_definition, list_external_provider_ids
from config.settings import set_api_key
from engine.security_posture import (
    inspect_security_posture,
    resolve_security_posture,
    set_security_check_override,
)
from engine.setup_state import (
    build_privacy_preferences,
    build_recommended_profiles,
    get_app_settings,
    get_provider_statuses,
    resolve_profile_backend,
    update_app_settings,
)
from server.db import get_db_connection
from server.models.schemas import (
    PrivacyPreferenceOption,
    PrivacyProfileOption,
    ProviderCredentialField,
    ProviderCredentialRequest,
    ProviderStatusResponse,
    SecurityCheckResponse,
    SecurityCheckOverrideRequest,
    SecurityPostureResponse,
    SetupOptionsResponse,
    SetupProfileRequest,
)

router = APIRouter(tags=["setup"])
_SUPPORTED_PROVIDERS = set(list_external_provider_ids())


def _provider_status_response(status) -> ProviderStatusResponse:
    return ProviderStatusResponse(
        provider=status.provider,
        label=status.label,
        deployment=cast(Any, getattr(status, "deployment", "local" if status.is_local else "external")),
        trust_boundary=cast(
            Any,
            getattr(status, "trust_boundary", "self_hosted" if status.is_local else "external"),
        ),
        auth_strategy=cast(Any, getattr(status, "auth_strategy", "none" if status.is_local else "api_key")),
        configured=status.configured,
        available=status.available,
        validated=status.validated,
        is_local=status.is_local,
        description=getattr(status, "description", None),
        setup_hint=getattr(status, "setup_hint", None),
        credential_fields=[
            ProviderCredentialField(
                name=field.name,
                label=field.label,
                input_type=field.input_type,
                placeholder=field.placeholder,
                secret=field.secret,
            )
            for field in getattr(status, "credential_fields", [])
        ],
        model=status.model,
        reason=status.reason,
    )


def _privacy_preference_option(option: dict[str, str]) -> PrivacyPreferenceOption:
    return PrivacyPreferenceOption(
        code=cast(Any, option["code"]),
        label=option["label"],
        description=option["description"],
    )


def _profile_option(profile: dict[str, object]) -> PrivacyProfileOption:
    return PrivacyProfileOption(
        code=cast(Any, str(profile["code"])),
        label=str(profile["label"]),
        description=str(profile["description"]),
        default_backend=cast(Any, str(profile["default_backend"])),
        provider_scope=cast(Any, str(profile["provider_scope"])),
        provider_options=[str(provider) for provider in cast(list[object], profile["provider_options"])],
        recommended_provider=cast(str | None, profile["recommended_provider"]),
        recommendation_reason=str(profile["recommendation_reason"]),
        requires_external_provider=bool(profile["requires_external_provider"]),
        available=bool(profile["available"]),
        recommended=bool(profile["recommended"]),
    )


def _normalized_credentials(payload: ProviderCredentialRequest) -> dict[str, str]:
    credentials = dict(payload.credentials or {})
    if payload.api_key and "api_key" not in credentials:
        credentials["api_key"] = payload.api_key
    return {name: value.strip() for name, value in credentials.items() if value and value.strip()}


def _validate_credentials(provider: str, credentials: dict[str, str]) -> None:
    definition = get_provider_definition(provider)
    if definition.auth_strategy != "api_key":
        raise HTTPException(status_code=422, detail="this provider does not accept stored credentials")

    value = credentials.get("api_key", "").strip()
    if len(value) < 12:
        raise HTTPException(status_code=422, detail="api key is too short")
    if definition.key_prefix and not value.startswith(definition.key_prefix):
        raise HTTPException(
            status_code=422,
            detail=f"{definition.label.lower()} keys should start with {definition.key_prefix}",
        )


def _validate_selected_provider(profile_code: str, preferred_provider: str | None, profiles) -> None:
    if preferred_provider is None:
        return
    selected_profile = next((profile for profile in profiles if profile["code"] == profile_code), None)
    if selected_profile is None:
        raise HTTPException(status_code=422, detail="unknown profile")
    if preferred_provider not in cast(list[str], selected_profile["provider_options"]):
        raise HTTPException(status_code=422, detail="provider is not compatible with the selected profile")


@router.get("/setup/model-options", response_model=SetupOptionsResponse)
def model_options(request: Request) -> SetupOptionsResponse:
    """Return provider availability and recommended privacy profiles."""
    _ = request
    with get_db_connection() as conn:
        settings = get_app_settings(conn)
        statuses = get_provider_statuses(conn)
        provider_statuses = [_provider_status_response(status) for status in statuses]
        privacy_preference = cast(str | None, settings["privacy_preference"])
        profiles = [
            _profile_option(profile)
            for profile in build_recommended_profiles(statuses, privacy_preference)
        ]
        preference_options = [
            _privacy_preference_option(option) for option in build_privacy_preferences()
        ]
    return SetupOptionsResponse(
        providers=provider_statuses,
        privacy_preference=cast(Any, privacy_preference),
        privacy_preferences=preference_options,
        profiles=profiles,
        active_profile=cast(str | None, settings["active_profile"]),
        preferred_provider=cast(str | None, settings["preferred_provider"]),
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
    credentials = _normalized_credentials(payload)
    _validate_credentials(provider, credentials)
    set_api_key(provider, credentials["api_key"])
    with get_db_connection() as conn:
        statuses = {status.provider: status for status in get_provider_statuses(conn)}
        status = statuses[provider]
    return _provider_status_response(status)


@router.post("/setup/profile", response_model=SetupOptionsResponse)
def save_profile(payload: SetupProfileRequest, request: Request) -> SetupOptionsResponse:
    """Persist the chosen onboarding profile and preferred backend."""
    _ = request
    with get_db_connection() as conn:
        statuses = get_provider_statuses(conn)
        current_settings = get_app_settings(conn)
        privacy_preference = payload.privacy_preference or cast(
            str | None, current_settings["privacy_preference"]
        )
        profiles = build_recommended_profiles(statuses, privacy_preference)
        _validate_selected_provider(payload.profile, payload.preferred_provider, profiles)
        selected_profile = next(profile for profile in profiles if profile["code"] == payload.profile)
        preferred_backend = payload.preferred_backend or resolve_profile_backend(payload.profile, statuses)
        preferred_provider = payload.preferred_provider or cast(
            str | None, selected_profile["recommended_provider"]
        )
        update_app_settings(
            conn,
            privacy_preference=privacy_preference,
            active_profile=payload.profile,
            preferred_provider=preferred_provider,
            preferred_backend=preferred_backend,
            onboarding_completed=(
                payload.onboarding_completed
                if payload.onboarding_completed is not None
                else cast(bool, current_settings["onboarding_completed"])
            ),
        )
        settings = get_app_settings(conn)
        provider_statuses = [_provider_status_response(status) for status in statuses]
        profiles = [
            _profile_option(profile)
            for profile in build_recommended_profiles(
                statuses,
                cast(str | None, settings["privacy_preference"]),
            )
        ]
        preference_options = [
            _privacy_preference_option(option) for option in build_privacy_preferences()
        ]
    return SetupOptionsResponse(
        providers=provider_statuses,
        privacy_preference=cast(Any, settings["privacy_preference"]),
        privacy_preferences=preference_options,
        profiles=profiles,
        active_profile=cast(str | None, settings["active_profile"]),
        preferred_provider=cast(str | None, settings["preferred_provider"]),
        preferred_backend=cast(Any, settings["preferred_backend"]),
    )


@router.get("/setup/security-posture", response_model=SecurityPostureResponse)
def security_posture(request: Request) -> SecurityPostureResponse:
    """Return machine security recommendations with persisted manual confirmations."""
    _ = request
    with get_db_connection() as conn:
        posture = resolve_security_posture(conn)
    return SecurityPostureResponse(
        platform=str(posture["platform"]),
        supported=bool(posture["supported"]),
        checks=[SecurityCheckResponse(**check) for check in posture["checks"]],  # type: ignore[arg-type]
    )


@router.post("/setup/security-posture/checks/{check_code}", response_model=SecurityPostureResponse)
def update_security_check(
    check_code: str,
    payload: SecurityCheckOverrideRequest,
    request: Request,
) -> SecurityPostureResponse:
    """Persist a manual completion override for an unknown security check."""
    _ = request
    posture = inspect_security_posture()
    checks = {
        str(check["code"]): check
        for check in cast(list[dict[str, object]], posture["checks"])
        if isinstance(check, dict)
    }
    selected_check = checks.get(check_code)
    if selected_check is None:
        raise HTTPException(status_code=404, detail="security check not found")
    if str(selected_check.get("status")) != "unknown":
        raise HTTPException(
            status_code=422,
            detail="only checks with unknown status can be marked complete manually",
        )

    with get_db_connection() as conn:
        set_security_check_override(conn, check_code, is_complete=payload.completed)
        resolved = resolve_security_posture(conn)

    return SecurityPostureResponse(
        platform=str(resolved["platform"]),
        supported=bool(resolved["supported"]),
        checks=[SecurityCheckResponse(**check) for check in resolved["checks"]],  # type: ignore[arg-type]
    )
