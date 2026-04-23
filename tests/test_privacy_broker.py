"""Focused tests for engine/privacy_broker.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import ProviderConfig
import engine.privacy_broker as privacy_broker_module
from engine.privacy_broker import (
    AuditedExternalExtractionConsentRequiredError,
    AuditedRoutingViolationError,
    PrivacyBroker,
)
from engine.prompt_builder import RoutingViolationError


@pytest.fixture
def local_config():
    return ProviderConfig(
        provider="ollama",
        api_key=None,
        model="llama3.1:8b",
        is_local=True,
        arch="apple_silicon",
        ram_gb=36.0,
    )


@pytest.fixture
def external_config():
    return ProviderConfig(
        provider="anthropic",
        api_key="test-key",  # pragma: allowlist secret
        model="claude-sonnet-4-6",
        is_local=False,
        arch="apple_silicon",
        ram_gb=36.0,
    )


def test_local_backend_allows_grounded_query_generation(local_config, monkeypatch):
    monkeypatch.setattr(
        privacy_broker_module,
        "generate_response",
        lambda messages, provider_config, stream=False: "safe local answer",
    )

    result = PrivacyBroker(local_config).generate_grounded_response(
        [{"role": "user", "content": "Who am I?"}],
        attributes=[{"label": "fear_of_failure", "routing": "local_only"}],
    )

    assert result.content == "safe local answer"
    assert result.metadata.is_local is True
    assert result.metadata.task_type == "query_generation"
    assert result.metadata.blocked_external_attributes_count == 0
    assert result.metadata.routing_enforced is True
    assert result.metadata.attribute_count == 1
    assert result.metadata.contains_local_only_context is True
    assert result.metadata.decision == "allowed"


def test_external_backend_allows_external_ok_query_generation(external_config, monkeypatch):
    monkeypatch.setattr(
        privacy_broker_module,
        "generate_response",
        lambda messages, provider_config, stream=False: "allowed external answer",
    )

    result = PrivacyBroker(external_config).generate_grounded_response(
        [{"role": "user", "content": "What matters most to me?"}],
        attributes=[{"label": "career_goal", "routing": "external_ok"}],
    )

    assert result.content == "allowed external answer"
    assert result.metadata.provider == "anthropic"
    assert result.metadata.is_local is False
    assert result.metadata.blocked_external_attributes_count == 0
    assert result.metadata.routing_enforced is True
    assert result.metadata.attribute_count == 1
    assert result.metadata.decision == "allowed"


def test_external_backend_blocks_local_only_query_generation(external_config):
    with pytest.raises(
        AuditedRoutingViolationError,
        match="local_only attributes cannot be sent to external backends: fear_of_failure",
    ) as exc_info:
        PrivacyBroker(external_config).generate_grounded_response(
            [{"role": "user", "content": "Tell me about my fears"}],
            attributes=[
                {
                    "domain": "fears",
                    "label": "fear_of_failure",
                    "routing": "local_only",
                }
            ],
            retrieval_mode="simple",
        )
    assert isinstance(exc_info.value, RoutingViolationError)
    assert exc_info.value.audit.decision == "blocked"
    assert exc_info.value.audit.reason == "local_only_context_blocked_for_external_inference"
    assert exc_info.value.audit.contains_local_only_context is True
    assert exc_info.value.audit.blocked_external_attributes_count == 1
    assert exc_info.value.audit.retrieval_mode == "simple"


def test_external_backend_allows_query_when_local_only_was_stripped_upstream(external_config, monkeypatch):
    """Broker allows request when local_only data was stripped upstream (audit flag only)."""
    monkeypatch.setattr(
        privacy_broker_module,
        "generate_response",
        lambda messages, provider_config, stream=False: "answer with stripped context",
    )

    result = PrivacyBroker(external_config).generate_grounded_response(
        [{"role": "user", "content": "What are my goals?"}],
        attributes=[],
        contains_local_only_context=True,
        local_only_stripped_for_external=True,
        retrieval_mode="simple",
    )

    assert result.content == "answer with stripped context"
    assert result.metadata.decision == "allowed"
    assert result.metadata.contains_local_only_context is True
    assert result.metadata.local_only_stripped_for_external is True
    assert result.metadata.blocked_external_attributes_count == 0


def test_structured_extraction_returns_metadata(external_config, monkeypatch):
    monkeypatch.setattr(
        privacy_broker_module,
        "generate_response",
        lambda messages, provider_config, stream=False: '[{"domain":"goals"}]',
    )

    result = PrivacyBroker(external_config).extract_structured_attributes(
        [{"role": "user", "content": "I want to change jobs."}],
        allow_external_input=True,
    )

    assert result.content == '[{"domain":"goals"}]'
    assert result.metadata.task_type == "capture_extraction"
    assert result.metadata.provider == "anthropic"
    assert result.metadata.routing_enforced is False
    assert result.metadata.blocked_external_attributes_count == 0
    assert result.metadata.attribute_count == 0
    assert result.metadata.external_input_allowed_by_user is True


def test_structured_extraction_requires_explicit_consent_for_external_backend(external_config):
    with pytest.raises(AuditedExternalExtractionConsentRequiredError) as exc_info:
        PrivacyBroker(external_config).extract_structured_attributes(
            [{"role": "user", "content": "I want to change jobs."}],
        )

    assert exc_info.value.audit.reason == "external_extraction_consent_required"
    assert exc_info.value.audit.decision == "blocked"


def test_interview_extraction_returns_metadata(local_config, monkeypatch):
    monkeypatch.setattr(
        privacy_broker_module,
        "extract_attributes",
        lambda question, answer, provider_config: [{"label": "recharge_style"}],
    )

    result = PrivacyBroker(local_config).extract_interview_attributes(
        "How do you recharge?",
        "Quiet time alone helps me reset.",
    )

    assert result.content == [{"label": "recharge_style"}]
    assert result.metadata.provider == "ollama"
    assert result.metadata.is_local is True
    assert result.metadata.task_type == "interview_extraction"
    assert result.metadata.routing_enforced is False
    assert result.metadata.decision == "allowed"


# ---------------------------------------------------------------------------
# Private server trust tier
# ---------------------------------------------------------------------------

@pytest.fixture
def private_server_config():
    return ProviderConfig(
        provider="private_server",
        api_key=None,
        model="llama3.1:8b",
        is_local=False,
        base_url="http://10.0.0.1:11434",
        arch="intel_mac",
        ram_gb=16.0,
    )


def test_private_server_allows_local_only_attributes(private_server_config, monkeypatch):
    monkeypatch.setattr(
        privacy_broker_module,
        "generate_response",
        lambda messages, config, **kwargs: "ok",
    )
    local_only_attr = {"label": "core_value", "routing": "local_only"}
    result = PrivacyBroker(private_server_config).generate_grounded_response(
        [{"role": "user", "content": "test"}],
        attributes=[local_only_attr],
    )
    assert result.metadata.decision == "allowed"
    assert result.metadata.is_private_server is True
    assert result.metadata.blocked_external_attributes_count == 0


def test_private_server_still_requires_input_consent(private_server_config):
    with pytest.raises(AuditedExternalExtractionConsentRequiredError) as exc_info:
        PrivacyBroker(private_server_config).extract_structured_attributes(
            [{"role": "user", "content": "sensitive raw input"}],
            allow_external_input=False,
        )
    assert exc_info.value.audit.reason == "external_extraction_consent_required"


def test_private_server_allows_extraction_with_consent(private_server_config, monkeypatch):
    monkeypatch.setattr(
        privacy_broker_module,
        "generate_response",
        lambda messages, config, **kwargs: '{"label": "x"}',
    )
    result = PrivacyBroker(private_server_config).extract_structured_attributes(
        [{"role": "user", "content": "raw input"}],
        allow_external_input=True,
    )
    assert result.metadata.decision == "allowed"
    assert result.metadata.external_input_allowed_by_user is True


def test_external_provider_still_blocked_from_local_only(external_config):
    local_only_attr = {"label": "secret", "routing": "local_only"}
    with pytest.raises(AuditedRoutingViolationError) as exc_info:
        PrivacyBroker(external_config).generate_grounded_response(
            [{"role": "user", "content": "test"}],
            attributes=[local_only_attr],
        )
    assert exc_info.value.audit.reason == "local_only_context_blocked_for_external_inference"
