"""Shared provider metadata for setup flows and router resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ProviderDeployment = Literal["local", "external"]
ProviderTrustBoundary = Literal["self_hosted", "external"]
ProviderAuthStrategy = Literal["none", "api_key"]
CredentialInputType = Literal["password", "text"]


@dataclass(frozen=True)
class CredentialField:
    """One credential field required to authenticate with a provider."""

    name: str
    label: str
    input_type: CredentialInputType = "password"
    placeholder: str | None = None
    secret: bool = True


@dataclass(frozen=True)
class ProviderDefinition:
    """Static metadata for one supported inference provider."""

    provider: str
    label: str
    deployment: ProviderDeployment
    trust_boundary: ProviderTrustBoundary
    auth_strategy: ProviderAuthStrategy
    default_model: str | None = None
    keyring_username: str | None = None
    key_prefix: str | None = None
    description: str = ""
    setup_hint: str | None = None
    credential_fields: tuple[CredentialField, ...] = field(default_factory=tuple)


PROVIDER_DEFINITIONS: dict[str, ProviderDefinition] = {
    "ollama": ProviderDefinition(
        provider="ollama",
        label="Ollama",
        deployment="local",
        trust_boundary="self_hosted",
        auth_strategy="none",
        description="Runs models on this device through your local Ollama runtime.",
        setup_hint="Install Ollama locally and keep the recommended model pulled on this machine.",
    ),
    "anthropic": ProviderDefinition(
        provider="anthropic",
        label="Anthropic",
        deployment="external",
        trust_boundary="external",
        auth_strategy="api_key",
        default_model="claude-sonnet-4-6",
        keyring_username="anthropic-api-key",
        key_prefix="sk-ant-",
        description="Cloud-hosted model access through Anthropic's managed API.",
        setup_hint="Store your Anthropic API key in the system keychain.",
        credential_fields=(
            CredentialField(
                name="api_key",
                label="Anthropic API key",
                placeholder="sk-ant-...",
            ),
        ),
    ),
    "groq": ProviderDefinition(
        provider="groq",
        label="Groq",
        deployment="external",
        trust_boundary="external",
        auth_strategy="api_key",
        default_model="llama-3.1-8b-instant",
        keyring_username="groq-api-key",
        key_prefix="gsk_",
        description="Cloud-hosted low-latency inference through Groq's managed API.",
        setup_hint="Store your Groq API key in the system keychain.",
        credential_fields=(
            CredentialField(
                name="api_key",
                label="Groq API key",
                placeholder="gsk_...",
            ),
        ),
    ),
}


def get_provider_definition(provider: str) -> ProviderDefinition:
    """Return metadata for one supported provider."""
    return PROVIDER_DEFINITIONS[provider]


def list_provider_definitions() -> list[ProviderDefinition]:
    """Return providers in display / preference order."""
    return [PROVIDER_DEFINITIONS[name] for name in ("ollama", "anthropic", "groq")]


def list_external_provider_ids() -> list[str]:
    """Return supported externally hosted provider ids in priority order."""
    return [
        definition.provider
        for definition in list_provider_definitions()
        if definition.deployment == "external"
    ]

