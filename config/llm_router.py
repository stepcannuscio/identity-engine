"""
llm_router.py — Hardware-aware LLM backend router for the identity-engine.

This module is a standalone, importable utility with zero knowledge of the
identity store, database, or schema. It detects local hardware, resolves the
best available inference backend, and exposes unified extraction and response
generation helpers, including streaming support for the FastAPI server.

Usage:
    from config.llm_router import (
        resolve_router, extract_attributes, generate_response, print_routing_report
    )

    config = resolve_router()
    print_routing_report(config)
    attrs = extract_attributes(question, answer, config)
    text = generate_response(messages, config)
"""

import json
import logging
import platform
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, cast

import requests

from config.provider_catalog import get_provider_definition, list_external_provider_ids
# All keychain reads go through settings.get_api_key — never call keyring here.
from config.settings import get_api_key

logger = logging.getLogger(__name__)
_STARTED_OLLAMA_PROCESS: object | None = None

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigurationError(Exception):
    """Raised when no usable LLM backend can be resolved."""


class ExtractionError(Exception):
    """Raised when JSON extraction fails after all retries."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIER_MODELS = {
    "local_large": "llama3.1:8b",
    "local_small": "llama3.2:3b",
}

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_TIMEOUT = 120

EXTRACT_SYSTEM_PROMPT = (
    "You are a structured data extractor for a personal identity store. "
    "Given the user's answer to an identity question, extract one or more identity attributes. "
    "For each attribute output a JSON object with these exact fields:\n"
    "- label: short snake_case identifier (e.g. 'recharge_style')\n"
    "- value: a clear, specific description in first person where natural (1-3 sentences max)\n"
    "- elaboration: any nuance or context worth preserving, or null\n"
    "- mutability: 'stable' or 'evolving'\n"
    "- confidence: float between 0.0 and 1.0\n\n"
    "Return a JSON array of attribute objects. Return JSON only. "
    "No preamble, no explanation, no markdown fences."
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    provider: str | None      # "anthropic", "groq", "ollama", or None
    api_key: str | None
    model: str
    is_local: bool
    # Metadata for the startup report
    arch: str = ""
    ram_gb: float = 0.0

# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------


def detect_hardware() -> dict:
    """Detect local hardware and return a capability summary.

    Returns:
        {
            "arch": "apple_silicon" | "intel_mac" | "linux_gpu" | "other",
            "ram_gb": float,
            "cpu_cores": int,
            "has_metal": bool,
            "has_cuda": bool,
            "recommended_tier": "local_large" | "local_small" | "api",
        }
    """
    machine = platform.machine().lower()
    processor = platform.processor().lower()
    system = platform.system()

    # Determine architecture
    if machine == "arm64" and system == "Darwin":
        arch = "apple_silicon"
    elif machine in ("x86_64", "amd64") and system == "Darwin":
        arch = "intel_mac"
    elif "cuda" in processor or _has_nvidia():
        arch = "linux_gpu"
    else:
        arch = "other"

    has_metal = arch == "apple_silicon"
    has_cuda = arch == "linux_gpu"

    # RAM detection via psutil
    ram_gb = 0.0
    cpu_cores = 0
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 0
    except ImportError:
        logger.warning(
            "psutil is not installed — cannot detect RAM. "
            "Defaulting recommended_tier to 'api'. "
            "Install psutil: pip install psutil"
        )
        return {
            "arch": arch,
            "ram_gb": 0.0,
            "cpu_cores": 0,
            "has_metal": has_metal,
            "has_cuda": has_cuda,
            "recommended_tier": "api",
        }

    # Tier recommendation
    if arch == "apple_silicon":
        if ram_gb >= 16:
            tier = "local_large"
        else:
            tier = "local_small"
    elif arch in ("intel_mac", "other"):
        if ram_gb >= 16:
            tier = "local_small"
        else:
            tier = "api"
    else:
        # linux_gpu or unknown
        tier = "local_small" if ram_gb >= 8 else "api"

    return {
        "arch": arch,
        "ram_gb": ram_gb,
        "cpu_cores": cpu_cores,
        "has_metal": has_metal,
        "has_cuda": has_cuda,
        "recommended_tier": tier,
    }


def _has_nvidia() -> bool:
    """Return True if nvidia-smi is available and reports a GPU."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------


def _ollama_is_running() -> bool:
    try:
        requests.get(OLLAMA_BASE_URL, timeout=2)
        return True
    except Exception:
        return False


def _ollama_has_model(model: str) -> bool:
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        tag = model.split(":")[0]
        return any(m.startswith(model) or m.startswith(tag + ":") for m in models)
    except Exception:
        return False


def _start_ollama(log_path=None) -> object | None:
    """Attempt to start the Ollama server. Returns Popen or None on failure."""
    try:
        kwargs: dict[str, Any] = {"start_new_session": True}
        fh = None
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(log_path, "a")
            kwargs.update(stdout=fh, stderr=fh)
        else:
            kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        process = subprocess.Popen(["ollama", "serve"], **kwargs)
        if fh is not None:
            fh.close()  # subprocess has inherited the fd; parent copy is no longer needed
    except FileNotFoundError:
        return None

    deadline = time.time() + 15
    while time.time() < deadline:
        if _ollama_is_running():
            return process
        time.sleep(0.5)

    process.terminate()
    return None


def _pull_model(model: str) -> bool:
    """Pull a model via Ollama CLI. Returns True on success."""
    try:
        result = subprocess.run(["ollama", "pull", model], timeout=300)
        return result.returncode == 0
    except Exception:
        return False


def _ensure_local_model(model: str) -> bool:
    """Ensure Ollama is running and the model is available. Returns True on success."""
    global _STARTED_OLLAMA_PROCESS

    if not _ollama_is_running():
        proc = _start_ollama()
        if proc is None:
            return False
        _STARTED_OLLAMA_PROCESS = proc

    if _ollama_has_model(model):
        return True

    # Try to pull the model
    print(f"  Pulling {model} — this may take a few minutes on first run...")
    return _pull_model(model)

# ---------------------------------------------------------------------------
# Router resolution
# ---------------------------------------------------------------------------


def _resolve_local_router(hw: dict[str, Any]) -> ProviderConfig:
    tier = hw["recommended_tier"]
    if tier not in ("local_large", "local_small"):
        raise ConfigurationError("Local inference is not available on this hardware.")

    model = TIER_MODELS[tier]
    if not _ensure_local_model(model):
        raise ConfigurationError("Local Ollama inference is not available.")

    return ProviderConfig(
        provider="ollama",
        api_key=None,
        model=model,
        is_local=True,
        arch=hw["arch"],
        ram_gb=hw["ram_gb"],
    )


def resolve_local_router() -> ProviderConfig:
    """Resolve a local-only Ollama backend or raise ConfigurationError."""
    return _resolve_local_router(detect_hardware())


def _resolve_external_router(
    hw: dict[str, Any],
    preferred_provider: str | None = None,
) -> ProviderConfig:
    ordered_providers = list_external_provider_ids()
    if preferred_provider:
        ordered_providers = [
            preferred_provider,
            *[provider for provider in ordered_providers if provider != preferred_provider],
        ]

    for provider in ordered_providers:
        definition = get_provider_definition(provider)
        api_key = get_api_key(provider)
        if not api_key:
            continue
        return ProviderConfig(
            provider=provider,
            api_key=api_key,
            model=definition.default_model or "",
            is_local=False,
            arch=hw["arch"],
            ram_gb=hw["ram_gb"],
        )

    raise ConfigurationError(
        "No external LLM backend is available.\n\n"
        "Options:\n"
        "  1. Add an Anthropic API key:  make add-anthropic-key KEY=sk-ant-...\n"
        "  2. Add a Groq API key:        make add-groq-key KEY=gsk_...\n"
    )


def resolve_external_router(preferred_provider: str | None = None) -> ProviderConfig:
    """Resolve an external-only backend or raise ConfigurationError."""
    return _resolve_external_router(detect_hardware(), preferred_provider=preferred_provider)


def resolve_provider_router(provider: str) -> ProviderConfig:
    """Resolve a specific named provider or raise ConfigurationError."""
    hw = detect_hardware()
    if provider == "ollama":
        return _resolve_local_router(hw)
    if provider in list_external_provider_ids():
        return _resolve_external_router(hw, preferred_provider=provider)
    raise ConfigurationError(f"Unknown provider: {provider!r}")


def resolve_router() -> ProviderConfig:
    """Detect hardware, resolve the best available backend, return ProviderConfig.

    Resolution order:
      1. Local Ollama if hardware supports it and model is available/pullable
      2. Anthropic API key from keychain
      3. Groq API key from keychain
      4. Raise ConfigurationError

    Raises:
        ConfigurationError: when no usable backend is found.
    """
    hw = detect_hardware()

    # Try local first
    if hw["recommended_tier"] in ("local_large", "local_small"):
        try:
            return _resolve_local_router(hw)
        except ConfigurationError as exc:
            logger.warning("Local LLM resolution failed: %s", exc)

    # Try API providers in preference order
    try:
        return _resolve_external_router(hw)
    except ConfigurationError:
        pass

    # Nothing available
    raise ConfigurationError(
        "No LLM backend is available.\n\n"
        "Options:\n"
        "  1. Install Ollama (https://ollama.com) for local inference.\n"
        "  2. Add an Anthropic API key:  make add-anthropic-key KEY=sk-ant-...\n"
        "  3. Add a Groq API key:        make add-groq-key KEY=gsk_...\n"
    )

# ---------------------------------------------------------------------------
# Unified inference
# ---------------------------------------------------------------------------


def _build_messages(question: str, answer: str, retry: bool = False) -> list[dict]:
    user_content = f"Question: {question}\n\nAnswer: {answer}"
    if retry:
        user_content += "\n\nReturn valid JSON array only. No other text."
    return [
        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _parse_json_response(raw: str) -> list:
    """Strip markdown fences and parse JSON array from a raw LLM response."""
    content = raw.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        content = "\n".join(lines).strip()
    return json.loads(content)


def _call_ollama(messages: list[dict], model: str, timeout: int = OLLAMA_TIMEOUT) -> str:
    payload = {"model": model, "messages": messages, "stream": False}
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=timeout
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def _stream_ollama(
    messages: list[dict], model: str, timeout: int = OLLAMA_TIMEOUT
) -> Generator[str, None, None]:
    payload = {"model": model, "messages": messages, "stream": True}
    with requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=timeout,
        stream=True,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            chunk = json.loads(line.decode("utf-8"))
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield str(content)


def _call_anthropic(
    messages: list[dict], model: str, api_key: str, timeout: int = OLLAMA_TIMEOUT
) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    # Separate system from user messages
    system_content = ""
    user_messages = []
    for m in messages:
        if m["role"] == "system":
            system_content = m["content"]
        else:
            user_messages.append(m)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_content,
        messages=user_messages,  # type: ignore[arg-type]
    )
    return response.content[0].text.strip()  # type: ignore[union-attr]


def _stream_anthropic(
    messages: list[dict], model: str, api_key: str, timeout: int = OLLAMA_TIMEOUT
) -> Generator[str, None, None]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    system_content = ""
    user_messages = []
    for message in messages:
        if message["role"] == "system":
            system_content = message["content"]
        else:
            user_messages.append(message)

    with client.messages.stream(
        model=model,
        max_tokens=1024,
        system=system_content,
        messages=user_messages,  # type: ignore[arg-type]
    ) as stream:
        for text in stream.text_stream:
            if text:
                yield text


def _call_groq(
    messages: list[dict], model: str, api_key: str, timeout: int = OLLAMA_TIMEOUT
) -> str:
    from groq import Groq
    client = Groq(api_key=api_key, timeout=timeout)
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=1024,
    )
    content = response.choices[0].message.content or ""
    return content.strip()


def _stream_groq(
    messages: list[dict], model: str, api_key: str, timeout: int = OLLAMA_TIMEOUT
) -> Generator[str, None, None]:
    from groq import Groq

    client = Groq(api_key=api_key, timeout=timeout)
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=1024,
        stream=True,
    )
    for chunk in response:
        if not hasattr(chunk, "choices"):
            continue
        chunk_data = cast(Any, chunk)
        delta = chunk_data.choices[0].delta.content or ""
        if delta:
            yield delta


def extract_attributes(question: str, answer: str, config: ProviderConfig) -> list[dict]:
    """Extract structured identity attributes from a question/answer pair.

    Routes to the correct backend based on config.is_local and config.provider.
    Retries once on JSON parse failure with an explicit JSON instruction appended.

    Args:
        question: The interview question asked of the user.
        answer:   The user's free-text answer.
        config:   ProviderConfig returned by resolve_router().

    Returns:
        List of attribute dicts with keys: label, value, elaboration, mutability, confidence.

    Raises:
        ExtractionError: when JSON parsing fails on both the initial attempt and the retry.
    """
    for attempt in range(2):
        retry = attempt == 1
        messages = _build_messages(question, answer, retry=retry)

        if config.is_local:
            raw = _call_ollama(messages, config.model)
        elif config.provider == "anthropic":
            assert config.api_key is not None
            raw = _call_anthropic(messages, config.model, config.api_key)
        elif config.provider == "groq":
            assert config.api_key is not None
            raw = _call_groq(messages, config.model, config.api_key)
        else:
            raise ConfigurationError(f"Unknown provider: {config.provider!r}")

        try:
            return _parse_json_response(raw)
        except (json.JSONDecodeError, ValueError):
            if attempt == 0:
                continue  # retry with stricter prompt
            raise ExtractionError(
                f"Failed to parse JSON response after 2 attempts.\n"
                f"Raw response: {raw[:800]}"
            )

    # Unreachable, but satisfies type checkers
    raise ExtractionError("Extraction loop exited without result.")


def generate_response(
    messages: list[dict],
    config: ProviderConfig,
    stream: bool = False,
    timeout_seconds: int | None = None,
) -> str | Generator[str, None, None]:
    """Generate a plain-text response from a full message array.

    Uses the same backend routing policy as extract_attributes() and enforces
    a 120-second timeout across all providers.
    """
    resolved_timeout = timeout_seconds or 120

    if config.is_local:
        if stream:
            return _stream_ollama(messages, config.model, timeout=resolved_timeout)
        return _call_ollama(messages, config.model, timeout=resolved_timeout)
    if config.provider == "anthropic":
        assert config.api_key is not None
        if stream:
            return _stream_anthropic(
                messages, config.model, config.api_key, timeout=resolved_timeout
            )
        return _call_anthropic(
            messages, config.model, config.api_key, timeout=resolved_timeout
        )
    if config.provider == "groq":
        assert config.api_key is not None
        if stream:
            return _stream_groq(
                messages, config.model, config.api_key, timeout=resolved_timeout
            )
        return _call_groq(messages, config.model, config.api_key, timeout=resolved_timeout)
    raise ConfigurationError(f"Unknown provider: {config.provider!r}")


def shutdown_started_ollama() -> None:
    """Terminate the Ollama process started by this module, if any."""
    global _STARTED_OLLAMA_PROCESS

    process = _STARTED_OLLAMA_PROCESS
    if process is None:
        return

    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        terminate()

    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            wait(timeout=5)
        except Exception:
            kill = getattr(process, "kill", None)
            if callable(kill):
                kill()

    _STARTED_OLLAMA_PROCESS = None

# ---------------------------------------------------------------------------
# Startup report
# ---------------------------------------------------------------------------


def print_routing_report(config: ProviderConfig) -> None:
    """Print a single-line summary of the resolved backend at startup."""
    ram_str = f"{config.ram_gb:.0f}GB" if config.ram_gb > 0 else "unknown RAM"
    arch_labels = {
        "apple_silicon": "Apple Silicon",
        "intel_mac":     "Intel Mac",
        "linux_gpu":     "Linux GPU",
        "other":         "this hardware",
    }
    arch_label = arch_labels.get(config.arch, config.arch)

    if config.is_local:
        line = f"Running locally   {config.model:<22} ({arch_label}, {ram_str})"
    else:
        reason = "local model not available on this hardware"
        line = (
            f"Running via API   {config.model:<22} ({config.provider})"
            f" — {reason}"
        )

    width = max(len(line) + 4, 60)
    print("─" * width)
    print(f"  {line}")
    print("─" * width)
