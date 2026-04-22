"""Tests for config/llm_router.py.

All tests are unit tests that mock external I/O (psutil, keyring, HTTP, subprocesses).
No real Ollama server or API key is required.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import (
    ConfigurationError,
    ExtractionError,
    ProviderConfig,
    detect_hardware,
    extract_attributes,
    print_routing_report,
    resolve_router,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_TIERS = {"local_large", "local_small", "api"}
VALID_ARCHS = {"apple_silicon", "intel_mac", "linux_gpu", "other"}

SAMPLE_ATTRS = [
    {
        "label": "recharge_style",
        "value": "I recharge by spending time alone.",
        "elaboration": None,
        "mutability": "stable",
        "confidence": 0.9,
    }
]

_MOCK_PSUTIL = MagicMock()
_MOCK_PSUTIL.virtual_memory.return_value = MagicMock(total=32 * 1024 ** 3)
_MOCK_PSUTIL.cpu_count.return_value = 8


def _make_config(
    provider="ollama",
    api_key=None,
    model="llama3.1:8b",
    is_local=True,
    arch="apple_silicon",
    ram_gb=36.0,
) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        is_local=is_local,
        arch=arch,
        ram_gb=ram_gb,
    )


# ---------------------------------------------------------------------------
# detect_hardware()
# ---------------------------------------------------------------------------

class TestDetectHardware:
    def test_returns_all_required_keys(self):
        with patch.dict("sys.modules", {"psutil": _MOCK_PSUTIL}):
            hw = detect_hardware()
        required = {"arch", "ram_gb", "cpu_cores", "has_metal", "has_cuda", "recommended_tier"}
        assert required.issubset(hw.keys())

    def test_recommended_tier_is_valid(self):
        with patch.dict("sys.modules", {"psutil": _MOCK_PSUTIL}):
            hw = detect_hardware()
        assert hw["recommended_tier"] in VALID_TIERS

    def test_arch_is_valid(self):
        with patch.dict("sys.modules", {"psutil": _MOCK_PSUTIL}):
            hw = detect_hardware()
        assert hw["arch"] in VALID_ARCHS

    def test_apple_silicon_large_ram_gives_local_large(self):
        big_ram = MagicMock()
        big_ram.virtual_memory.return_value = MagicMock(total=36 * 1024 ** 3)
        big_ram.cpu_count.return_value = 10
        with (
            patch("platform.machine", return_value="arm64"),
            patch("platform.system", return_value="Darwin"),
            patch.dict("sys.modules", {"psutil": big_ram}),
        ):
            hw = detect_hardware()
        assert hw["recommended_tier"] == "local_large"
        assert hw["has_metal"] is True

    def test_apple_silicon_small_ram_gives_local_small(self):
        small_ram = MagicMock()
        small_ram.virtual_memory.return_value = MagicMock(total=8 * 1024 ** 3)
        small_ram.cpu_count.return_value = 8
        with (
            patch("platform.machine", return_value="arm64"),
            patch("platform.system", return_value="Darwin"),
            patch.dict("sys.modules", {"psutil": small_ram}),
        ):
            hw = detect_hardware()
        assert hw["recommended_tier"] == "local_small"

    def test_intel_mac_with_16gb_gives_local_small(self):
        med_ram = MagicMock()
        med_ram.virtual_memory.return_value = MagicMock(total=16 * 1024 ** 3)
        med_ram.cpu_count.return_value = 8
        with (
            patch("platform.machine", return_value="x86_64"),
            patch("platform.system", return_value="Darwin"),
            patch.dict("sys.modules", {"psutil": med_ram}),
        ):
            hw = detect_hardware()
        assert hw["recommended_tier"] == "local_small"
        assert hw["has_metal"] is False

    def test_intel_mac_with_8gb_gives_api(self):
        low_ram = MagicMock()
        low_ram.virtual_memory.return_value = MagicMock(total=8 * 1024 ** 3)
        low_ram.cpu_count.return_value = 4
        with (
            patch("platform.machine", return_value="x86_64"),
            patch("platform.system", return_value="Darwin"),
            patch.dict("sys.modules", {"psutil": low_ram}),
        ):
            hw = detect_hardware()
        assert hw["recommended_tier"] == "api"

    def test_psutil_unavailable_defaults_to_api(self):
        # Simulate ImportError for psutil
        with patch.dict("sys.modules", {"psutil": None}):
            hw = detect_hardware()
        assert hw["recommended_tier"] == "api"
        assert hw["ram_gb"] == 0.0


# ---------------------------------------------------------------------------
# resolve_router()
# ---------------------------------------------------------------------------

class TestResolveRouter:
    def _patch_local_success(self, model="llama3.1:8b"):
        """Return a context manager stack that makes local Ollama resolve successfully."""
        return [
            patch("config.llm_router.detect_hardware", return_value={
                "arch": "apple_silicon", "ram_gb": 36.0, "cpu_cores": 10,
                "has_metal": True, "has_cuda": False, "recommended_tier": "local_large",
            }),
            patch("config.llm_router._ensure_local_model", return_value=True),
        ]

    def test_local_path_returns_is_local_true(self):
        with (
            patch("config.llm_router.detect_hardware", return_value={
                "arch": "apple_silicon", "ram_gb": 36.0, "cpu_cores": 10,
                "has_metal": True, "has_cuda": False, "recommended_tier": "local_large",
            }),
            patch("config.llm_router._ensure_local_model", return_value=True),
        ):
            config = resolve_router()
        assert config.is_local is True
        assert config.provider == "ollama"

    def test_raises_configuration_error_when_no_backend(self):
        with (
            patch("config.llm_router.detect_hardware", return_value={
                "arch": "intel_mac", "ram_gb": 8.0, "cpu_cores": 4,
                "has_metal": False, "has_cuda": False, "recommended_tier": "api",
            }),
            patch("config.llm_router.get_api_key", return_value=None),
        ):
            with pytest.raises(ConfigurationError):
                resolve_router()

    def test_falls_back_to_anthropic_when_local_fails(self):
        with (
            patch("config.llm_router.detect_hardware", return_value={
                "arch": "apple_silicon", "ram_gb": 36.0, "cpu_cores": 10,
                "has_metal": True, "has_cuda": False, "recommended_tier": "local_large",
            }),
            patch("config.llm_router._ensure_local_model", return_value=False),
            patch(
                "config.llm_router.get_api_key",
                side_effect=(  # pragma: allowlist secret
                    lambda p: "sk-ant-test" if p == "anthropic" else None
                ),
            ),
        ):
            config = resolve_router()
        assert config.is_local is False
        assert config.provider == "anthropic"

    def test_logs_local_resolution_failure_before_fallback(self, caplog):
        with (
            patch("config.llm_router.detect_hardware", return_value={
                "arch": "apple_silicon", "ram_gb": 36.0, "cpu_cores": 10,
                "has_metal": True, "has_cuda": False, "recommended_tier": "local_large",
            }),
            patch(
                "config.llm_router._resolve_local_router",
                side_effect=ConfigurationError("Ollama did not start"),
            ),
            patch(
                "config.llm_router.get_api_key",
                side_effect=(
                    lambda p: "sk-ant-test" if p == "anthropic" else None
                ),
            ),
        ):
            config = resolve_router()

        assert config.provider == "anthropic"
        assert "Local LLM resolution failed: Ollama did not start" in caplog.text

    def test_falls_back_to_groq_when_anthropic_missing(self):
        with (
            patch("config.llm_router.detect_hardware", return_value={
                "arch": "intel_mac", "ram_gb": 8.0, "cpu_cores": 4,
                "has_metal": False, "has_cuda": False, "recommended_tier": "api",
            }),
            patch(
                "config.llm_router.get_api_key",
                side_effect=(
                    lambda p: "gsk_test" if p == "groq" else None  # pragma: allowlist secret
                ),
            ),
        ):
            config = resolve_router()
        assert config.is_local is False
        assert config.provider == "groq"

    def test_local_model_matches_tier(self):
        with (
            patch("config.llm_router.detect_hardware", return_value={
                "arch": "apple_silicon", "ram_gb": 36.0, "cpu_cores": 10,
                "has_metal": True, "has_cuda": False, "recommended_tier": "local_large",
            }),
            patch("config.llm_router._ensure_local_model", return_value=True),
        ):
            config = resolve_router()
        assert config.model == "llama3.1:8b"


# ---------------------------------------------------------------------------
# extract_attributes()
# ---------------------------------------------------------------------------

QUESTION = "How do you recharge?"
ANSWER = "I spend time alone, reading."


class TestExtractAttributes:
    def test_local_path_returns_parsed_attributes(self):
        raw = json.dumps(SAMPLE_ATTRS)
        config = _make_config(is_local=True)
        with patch("config.llm_router._call_ollama", return_value=raw):
            result = extract_attributes(QUESTION, ANSWER, config)
        assert result == SAMPLE_ATTRS

    def test_anthropic_path_returns_parsed_attributes(self):
        raw = json.dumps(SAMPLE_ATTRS)
        config = _make_config(
            provider="anthropic", api_key="sk-ant-test", is_local=False  # pragma: allowlist secret
        )
        with patch("config.llm_router._call_anthropic", return_value=raw):
            result = extract_attributes(QUESTION, ANSWER, config)
        assert result == SAMPLE_ATTRS

    def test_groq_path_returns_parsed_attributes(self):
        raw = json.dumps(SAMPLE_ATTRS)
        config = _make_config(
            provider="groq", api_key="gsk_test", is_local=False  # pragma: allowlist secret
        )
        with patch("config.llm_router._call_groq", return_value=raw):
            result = extract_attributes(QUESTION, ANSWER, config)
        assert result == SAMPLE_ATTRS

    def test_retries_on_first_json_failure(self):
        bad_raw = "not json at all"
        good_raw = json.dumps(SAMPLE_ATTRS)
        config = _make_config(is_local=True)
        call_count = {"n": 0}

        def side_effect(messages, model, **kwargs):
            call_count["n"] += 1
            return bad_raw if call_count["n"] == 1 else good_raw

        with patch("config.llm_router._call_ollama", side_effect=side_effect):
            result = extract_attributes(QUESTION, ANSWER, config)
        assert call_count["n"] == 2
        assert result == SAMPLE_ATTRS

    def test_raises_extraction_error_on_two_consecutive_failures(self):
        config = _make_config(is_local=True)
        with patch("config.llm_router._call_ollama", return_value="not json"):
            with pytest.raises(ExtractionError) as exc_info:
                extract_attributes(QUESTION, ANSWER, config)
        assert "not json" in str(exc_info.value)

    def test_strips_markdown_fences(self):
        raw = "```json\n" + json.dumps(SAMPLE_ATTRS) + "\n```"
        config = _make_config(is_local=True)
        with patch("config.llm_router._call_ollama", return_value=raw):
            result = extract_attributes(QUESTION, ANSWER, config)
        assert result == SAMPLE_ATTRS


# ---------------------------------------------------------------------------
# print_routing_report()
# ---------------------------------------------------------------------------

class TestPrintRoutingReport:
    def test_local_config_does_not_raise(self, capsys):
        config = _make_config(is_local=True, arch="apple_silicon", ram_gb=36.0)
        print_routing_report(config)
        out = capsys.readouterr().out
        assert "locally" in out
        assert "llama3.1:8b" in out

    def test_api_config_does_not_raise(self, capsys):
        config = _make_config(
            provider="anthropic",
            api_key="sk-ant-x",  # pragma: allowlist secret
            model="claude-sonnet-4-6",
            is_local=False, arch="intel_mac", ram_gb=8.0,
        )
        print_routing_report(config)
        out = capsys.readouterr().out
        assert "API" in out
        assert "anthropic" in out

    def test_groq_config_does_not_raise(self, capsys):
        config = _make_config(
            provider="groq",
            api_key="gsk_x",  # pragma: allowlist secret
            model="llama-3.1-8b-instant",
            is_local=False, arch="intel_mac", ram_gb=16.0,
        )
        print_routing_report(config)
        out = capsys.readouterr().out
        assert "groq" in out

    def test_zero_ram_does_not_raise(self, capsys):
        config = _make_config(ram_gb=0.0)
        print_routing_report(config)  # should not raise

    def test_unknown_arch_does_not_raise(self, capsys):
        config = _make_config(arch="other")
        print_routing_report(config)  # should not raise

    def test_private_server_config_shows_url(self, capsys):
        config = ProviderConfig(
            provider="private_server",
            api_key=None,
            model="llama3.1:8b",
            is_local=False,
            base_url="http://100.10.0.1:11434",
        )
        print_routing_report(config)
        out = capsys.readouterr().out
        assert "private server" in out
        assert "100.10.0.1" in out


# ---------------------------------------------------------------------------
# Private server resolution
# ---------------------------------------------------------------------------

_INTEL_HW = {
    "arch": "intel_mac", "ram_gb": 16.0, "cpu_cores": 4,
    "has_metal": False, "has_cuda": False, "recommended_tier": "local_small",
}


class TestPrivateServerRouter:
    def test_returns_correct_config(self):
        with (
            patch("config.llm_router.get_private_server_url", return_value="http://10.0.0.1:11434"),
            patch("config.llm_router.requests.get"),
        ):
            from config.llm_router import _resolve_private_server_router
            config = _resolve_private_server_router(_INTEL_HW)
        assert config.provider == "private_server"
        assert config.is_local is False
        assert config.base_url == "http://10.0.0.1:11434"

    def test_is_trusted_private_property(self):
        config = ProviderConfig(
            provider="private_server", api_key=None, model="llama3.1:8b",
            is_local=False, base_url="http://10.0.0.1:11434",
        )
        assert config.is_trusted_private is True

    def test_is_trusted_private_false_for_external(self):
        config = _make_config(provider="anthropic", is_local=False)
        assert config.is_trusted_private is False

    def test_raises_when_url_not_configured(self):
        with patch("config.llm_router.get_private_server_url", return_value=None):
            from config.llm_router import _resolve_private_server_router, ConfigurationError
            with pytest.raises(ConfigurationError, match="Private server URL is not configured"):
                _resolve_private_server_router(_INTEL_HW)

    def test_raises_when_server_unreachable(self):
        import requests as _requests
        with (
            patch("config.llm_router.get_private_server_url", return_value="http://10.0.0.1:11434"),
            patch("config.llm_router.requests.get", side_effect=_requests.ConnectionError("refused")),
        ):
            from config.llm_router import _resolve_private_server_router, ConfigurationError
            with pytest.raises(ConfigurationError, match="not reachable"):
                _resolve_private_server_router(_INTEL_HW)

    def test_extract_attributes_routes_to_private_server_url(self):
        import json as _json
        raw = _json.dumps(SAMPLE_ATTRS)
        config = ProviderConfig(
            provider="private_server", api_key=None, model="llama3.1:8b",
            is_local=False, base_url="http://10.0.0.1:11434",
        )
        with patch("config.llm_router.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"message": {"content": raw}},
            )
            mock_post.return_value.raise_for_status = lambda: None
            result = extract_attributes(QUESTION, ANSWER, config)
        call_url = mock_post.call_args[0][0]
        assert "10.0.0.1" in call_url
        assert result == SAMPLE_ATTRS

    def test_resolve_router_prefers_private_server_when_configured(self):
        with (
            patch("config.llm_router.detect_hardware", return_value=_INTEL_HW),
            patch("config.llm_router.get_private_server_url", return_value="http://10.0.0.1:11434"),
            patch("config.llm_router.requests.get"),
            patch("config.llm_router.requests.get"),
        ):
            config = resolve_router()
        assert config.provider == "private_server"
