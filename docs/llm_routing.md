# LLM Routing

`config/llm_router.py` automatically selects the best available inference
backend for the current machine and exposes a single `extract_attributes()`
function used by all scripts. No script other than `llm_router.py` should
call an LLM directly.

---

## Hardware detection and tiers

`detect_hardware()` inspects the machine at startup and assigns a
`recommended_tier`:

| Hardware                       | RAM    | Tier          | Model          |
|-------------------------------|--------|---------------|----------------|
| Apple Silicon (arm64)         | ≥ 16 GB | `local_large` | `llama3.1:8b`  |
| Apple Silicon (arm64)         | 8–15 GB | `local_small` | `llama3.2:3b`  |
| Intel Mac / non-Metal         | ≥ 16 GB | `local_small` | `llama3.2:3b`  |
| Intel Mac / non-Metal         | < 16 GB | `api`         | (see below)    |
| psutil unavailable            | —       | `api`         | (see below)    |

For `local_large` and `local_small` tiers, the router first tries to run
the model locally via Ollama. If Ollama is not installed or the model
cannot be pulled, it falls through to the API tier.

---

## Provider preference order

When the API tier is used (either because hardware is insufficient or
local setup failed), the router checks the system keychain in this order:

1. **Anthropic** — `anthropic-api-key` → model `claude-sonnet-4-6`
2. **Groq**      — `groq-api-key`      → model `llama-3.1-8b-instant`

The local path is always preferred over API when available. This is not
configurable by design.

If no backend is reachable, `resolve_router()` raises `ConfigurationError`
with instructions for what to set up.

---

## Adding API keys

Keys are stored in the system keychain (macOS Keychain / Linux Secret
Service). They are never written to disk or printed in logs.

```bash
# Anthropic
make add-anthropic-key KEY=sk-ant-...

# Groq
make add-groq-key KEY=gsk_...
```

You can also set them manually:

```python
import keyring
keyring.set_password("identity-engine", "anthropic-api-key", "sk-ant-...")
keyring.set_password("identity-engine", "groq-api-key", "gsk_...")
```

---

## Overriding auto-detection

There is no configuration flag to force a specific tier — the router
always picks the best available option automatically.

To force a specific backend during development, you can call the
dataclass directly:

```python
from config.llm_router import ProviderConfig, extract_attributes

config = ProviderConfig(
    provider="ollama",
    api_key=None,
    model="llama3.2:3b",
    is_local=True,
)
attrs = extract_attributes(question, answer, config)
```

---

## Startup report

`print_routing_report(config)` prints a single summary line at script
startup so you always know which backend is active:

```
────────────────────────────────────────────────────────
  Running locally   llama3.1:8b            (Apple Silicon, 36GB)
────────────────────────────────────────────────────────

────────────────────────────────────────────────────────
  Running via API   claude-sonnet-4-6      (anthropic) — local model not available on this hardware
────────────────────────────────────────────────────────
```

---

## Key design constraints

- `llm_router.py` has zero knowledge of the identity store, database,
  or schema — it is purely an inference utility.
- All keychain reads go through `config/settings.py::get_api_key()`.
- API keys are never printed, logged, or included in error messages.
- The local path is always preferred over API.
