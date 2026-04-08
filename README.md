# identity-engine

A privacy-first personal identity store. Models who you are — values, goals,
personality, patterns — in a structured, queryable, encrypted local database.

## Status: Phase 1 — Foundation + Identity Seeding

Schema, security infrastructure, and interactive identity interview. No
application layer yet.

## Security model

- All data lives in `~/.identity-engine/identity.db` (never inside this repo)
- The database is encrypted with SQLCipher (AES-256)
- The encryption key is stored in the system keychain — never on disk in plaintext
- Pre-commit hooks block any attempt to commit `.db` files or secret-like strings
- Each attribute carries a `routing` flag: `local_only` (default) or `external_ok`
- API keys are stored in the system keychain and never logged, printed, or committed

## Quick start

```sh
make setup      # create venv, install deps, install pre-commit hooks
make init       # generate key, create database, seed domains
make test       # run the test suite
make interview  # start the interactive identity interview
make view       # pretty-print the identity store
```

## LLM backend

The interview script automatically selects the best available backend at
startup and prints a one-line summary so you always know what's running:

```
──────────────────────────────────────────────────────────────
  Running locally   llama3.1:8b            (Apple Silicon, 36GB)
──────────────────────────────────────────────────────────────
```

**Selection order — local is always preferred:**

| Hardware | RAM | Backend | Model |
|---|---|---|---|
| Apple Silicon | ≥ 16 GB | Ollama (local) | `llama3.1:8b` |
| Apple Silicon | 8–15 GB | Ollama (local) | `llama3.2:3b` |
| Intel Mac | ≥ 16 GB | Ollama (local) | `llama3.2:3b` |
| Intel Mac / other | < 16 GB | API | Anthropic or Groq |

If local Ollama is not installed or the model cannot be pulled, the router
falls back to API providers in this order: Anthropic → Groq. If neither is
configured, startup exits with clear instructions.

**Adding API keys (stored in system keychain, never on disk):**

```sh
make add-anthropic-key KEY=sk-ant-...
make add-groq-key KEY=gsk_...
```

See [docs/llm_routing.md](docs/llm_routing.md) for the full routing reference.

## Seeding your identity store

`make interview` launches a guided terminal interview across eight identity
domains: personality, values, goals, patterns, voice, relationships, fears,
and beliefs.

**How it works:**

1. The router detects your hardware and selects a backend automatically
2. You answer one question at a time in plain English
3. The LLM extracts structured attributes from your answer and shows you a
   numbered preview
4. You confirm, skip, edit, or retry before anything is written
5. Confirmed attributes are written to the database immediately — nothing is
   batched or written without your explicit approval
6. A `reflection_sessions` record is saved at the end of every session,
   including interrupted ones

**Before your first interview:**

```sh
make setup      # if you haven't already
make init
make interview  # Ollama is started and the model pulled automatically if needed
```

You can run the interview as many times as you like. Re-answering a question
whose label already exists prompts you to update (supersede) the old value or
skip — the full history is preserved in `attribute_history`.

## Inspecting the store

`make view` prints everything currently in the database, grouped by domain:

```
════════════════════════════════════════════════
  IDENTITY STORE  —  7 attributes across 3 domains
════════════════════════════════════════════════

── PERSONALITY (3) ──────────────────────────────
  decision_making  [stable, reflection, 0.90] local_only
    Deliberate and research-driven.

  recharge_style   [evolving, reflection, 0.80] local_only
    Introvert — quiet time after social events.
  ...

────────────────────────────────────────────────
  3 domains with data  ·  5 domains empty  ·  7 total attributes
  Last updated: 2026-04-06 14:23:01
────────────────────────────────────────────────
```

Only `active` attributes are shown. Superseded and retracted rows are retained
in `attribute_history` but excluded from the view.

See [docs/view_db.md](docs/view_db.md) for the full output format reference.

## Structure

```
config/settings.py          — paths, keychain access, routing and source constants
config/llm_router.py        — hardware detection, backend selection, unified inference
db/connection.py            — SQLCipher connection context manager
db/schema.py                — DDL and domain seeding
scripts/init_db.py          — one-time (idempotent) initialisation script
scripts/seed_interview.py   — interactive identity interview (make interview)
scripts/view_db.py          — terminal viewer for the identity store (make view)
tests/test_schema.py        — schema and constraint tests
tests/test_interview.py     — interview logic, DB helpers, and UI flow tests
tests/test_llm_router.py    — hardware detection, router resolution, and inference tests
tests/test_view_db.py       — viewer output and filtering tests
docs/schema.md              — full schema reference
docs/interview.md           — interview script reference
docs/llm_routing.md         — LLM routing reference and key setup guide
docs/view_db.md             — viewer output format reference
```

See [docs/schema.md](docs/schema.md) for the full schema reference.
See [docs/interview.md](docs/interview.md) for the interview script reference.
See [docs/llm_routing.md](docs/llm_routing.md) for the LLM routing reference.
See [docs/view_db.md](docs/view_db.md) for the viewer reference.
