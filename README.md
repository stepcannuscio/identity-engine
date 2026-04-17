# identity-engine

A privacy-first personal identity store. Models who you are — values, goals,
personality, patterns — in a structured, queryable, encrypted local database.

## Status: Phase 4 — Unified Onboarding + Teach

Schema, security infrastructure, unified onboarding/Teach flows, interactive
query/capture tooling, an HTTPS FastAPI backend, and a Vite-built React
frontend served by the backend in production.

## Security model

- All data lives in `~/.identity-engine/identity.db` (never inside this repo)
- The database is encrypted with SQLCipher (AES-256)
- The encryption key is stored in the system keychain — never on disk in plaintext
- Pre-commit hooks block any attempt to commit `.db` files or secret-like strings
- Each attribute carries a `routing` flag: `local_only` (default) or `external_ok`
- API keys are stored in the system keychain and never logged, printed, or committed
- The web server binds to a Tailscale IP when available and never to `0.0.0.0`
- The web server serves HTTPS only with a self-signed cert in `~/.identity-engine/certs/`
- The web UI passphrase is stored in the system keychain, never in a file

## Quick start

```sh
make setup      # create venv, install deps, install pre-commit hooks
make init       # generate key, create database, seed domains
make test       # run backend + frontend tests
make capture    # save a quick note directly as identity attributes
make query      # start interactive freeform query mode
make serve      # start the HTTPS FastAPI backend for the web UI
make frontend-install  # install frontend npm dependencies
make frontend-dev      # start the Vite frontend dev server
make frontend-build    # build the production frontend bundle
make dev        # start backend + frontend together
make smoke      # run a quick API smoke test against the backend
make view       # pretty-print the identity store
make set-ui-passphrase  # update the web UI passphrase
```

## Testing

`make test` runs all backend and frontend tests by default:

```sh
make test
```

To scope the run, pass `APP=backend`, `APP=frontend`, or `APP=all` together
with app-specific arguments:

```sh
make test APP=backend BACKEND_ARGS="-k llm_router"
make test APP=frontend FRONTEND_ARGS="src/test/use-auth.test.jsx"
make test APP=all BACKEND_ARGS="-k server" FRONTEND_ARGS="--coverage"
```

Under the hood:

- backend tests run with `pytest`
- frontend tests run with `vitest`
- `make frontend-install` installs frontend dependencies explicitly, but
  frontend test runs also install them automatically if needed

## LLM backend

The router automatically selects the best available backend at startup and
prints a one-line summary so you always know what's running:

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

## Teach and onboarding

The primary intake flow now lives in the web UI under the `Teach` tab.
On first login, the app opens Teach automatically until onboarding is complete.
After that, Teach stays available any time you want to add more context.

What Teach supports:

- Guided questions seeded from the canonical interview catalog
- Dynamically generated follow-up questions that use sanitized metadata only
- Skip and feedback controls such as `not relevant`, `duplicate`, and `too personal`
- Quick-note capture for low-friction teaching
- Tagged file uploads (`.txt`, `.md`, `.pdf`, `.docx`) parsed locally only
- Privacy/profile setup with recommended local/external model configurations
- macOS security recommendations for FileVault, recovery, lock behavior, and boot login

The underlying privacy guarantees remain the same:

- all LLM work still goes through `PrivacyBroker` and `config/llm_router.py`
- `local_only` data never leaves the system
- teach answers are extracted into structured attributes before persistence
- artifact bodies stay local and are used only as bounded local evidence

**Getting started:**

```sh
make setup      # if you haven't already
make init
make serve
make frontend-dev
```

Then log in to the UI and complete the Teach flow.

## Quick capture

`make capture` is the low-friction ingest path for short notes, observations,
or updates you want to save immediately without starting an interview session.

Usage:

```sh
make capture TEXT="your text here"
make capture TEXT="your text here" DOMAIN=goals
```

Arguments:

- `TEXT` — required quick-capture text
- `DOMAIN` — optional domain hint

Examples:

```sh
make capture TEXT="I've been waking up early naturally and feeling more focused in the mornings"
make capture TEXT="my main goal right now is landing a role in Seattle by end of summer" DOMAIN=goals
```

How it works:

1. Resolves the configured LLM backend and prints the routing summary
2. Extracts one or more attributes from the short note
3. Shows a single confirmation preview before any writes
4. Checks for active label conflicts inside the same domain
5. Writes confirmed attributes atomically with `source = explicit` and
   `routing = local_only`

Quick captures do not create a `reflection_sessions` row. They are sessionless,
atomic writes intended for “Post-it note” style ingestion.

## Artifact ingestion

`POST /artifacts` adds a first-pass knowledge intake layer for longer local
content such as notes, journals, transcripts, and tagged text/document uploads.

What it does:

1. Stores the raw extracted text locally in the encrypted database
2. Chunks the content into ordered retrieval units
3. Keeps artifacts separate from canonical attributes
4. Lets the query system pull in a few bounded local chunks when structured
   attributes are not enough
5. Stores normalized tags that can improve Teach recommendations and retrieval

Privacy constraints:

- artifact bodies remain local-only
- external backends are blocked if a response would rely on artifact chunks
- prompts never receive full documents, only a small bounded set of retrieved
  excerpts for local inference

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

## Querying the store

`make query` launches an interactive freeform question loop:

- Classifies each question (`simple` or `open_ended`)
- Retrieves and scores relevant active attributes from the encrypted store
- Builds a grounded prompt with capped conversation history (6 exchanges)
- Runs a deterministic coverage evaluator before inference and classifies the
  query as `high_confidence`, `medium_confidence`, `low_confidence`, or
  `insufficient_data`; low and medium cases append a brief hedge to the
  prompt, and `insufficient_data` returns a helpful explanation without
  calling the LLM
- Generates a concise answer through the resolved backend
- Writes one `reflection_sessions` record when the session exits

In-session commands:

- `history` — print current session history
- `clear` — clear current session history
- `status` — show query count, retrieved-attribute total, and backend
- `quit` / `q` — exit and persist session summary

See [docs/query.md](docs/query.md) for details.

## Web UI

The frontend lives in `frontend/` and is built with Vite + React. It never
touches the database or LLM providers directly; all reads and writes go
through the FastAPI API. The UI now includes:

- `Teach` for onboarding, provider setup, security recommendations, guided teaching, and uploads
- `Query` for grounded self-querying
- `Identity Graph` for reviewing and correcting attributes
- `History` for session and routing summaries

Development:

```sh
make frontend-install
make dev
```

Production:

```sh
make frontend-build
make serve
```

When `frontend/dist/` exists, `scripts/serve.py` mounts the built app at `/`
so a single HTTPS server on port `8443` serves both the API and the React UI.

## FastAPI backend

`make serve` starts the local HTTPS backend consumed by the React web UI.

Startup behaviour:

1. Detects a Tailscale IP from `tailscale0` or `utun*`
2. Falls back to `IDENTITY_ENGINE_BIND_IP`, then `127.0.0.1`
3. Refuses to bind to `0.0.0.0`
4. Generates `~/.identity-engine/certs/key.pem` and `cert.pem` on first run
5. Opens the encrypted database and resolves the active LLM backend
6. Prompts for a UI passphrase on first run and stores it in the keychain

Authentication:

- `POST /auth/login` issues an in-memory session token valid for 8 hours
- All routes except `POST /auth/login` and `GET /health` require auth
- Failed logins are rate-limited per IP
- Tokens are invalidated on server restart

Core routes:

- `GET /health`
- `POST /query`
- `POST /query/stream`
- `POST /artifacts`
- `GET/POST/PUT/DELETE /attributes...`
- `POST /capture/preview`
- `POST /capture` (also accepts approved preview items from the UI)
- `GET /sessions` (includes routing-log detail for the History tab)

See [docs/server.md](docs/server.md) for the full API reference.

## Smoke testing the API

After starting the backend with `make serve`, you can run a quick end-to-end
check with:

```sh
make smoke
```

The Python smoke script:

- prompts for the UI passphrase unless `PASSPHRASE` is set
- defaults to `https://127.0.0.1:8443`
- can target another server with `BASE_URL=https://100.x.x.x:8443`
- skips TLS verification by default because the server uses a self-signed cert

Examples:

```sh
make smoke
BASE_URL=https://100.x.x.x:8443 PASSPHRASE='your passphrase' make smoke
.venv/bin/python scripts/smoke_api.py --base-url https://127.0.0.1:8443
```

## Structure

```
config/settings.py          — paths, keychain access, routing and source constants
config/llm_router.py        — hardware detection, backend selection, unified inference
db/connection.py            — SQLCipher connection context manager
db/schema.py                — DDL and domain seeding
engine/privacy_broker.py    — application-level inference boundary and routing metadata
engine/query_classifier.py  — deterministic simple/open-ended query classification
engine/retriever.py         — score-based identity attribute retrieval
engine/artifact_ingestion.py — local artifact storage and chunking helpers
engine/artifact_retrieval.py — deterministic artifact chunk retrieval
engine/prompt_builder.py    — grounded system prompt + message assembly
engine/session.py           — in-memory session state and routing log
engine/query_engine.py      — end-to-end query orchestration
engine/capture.py           — quick-capture extraction, confirmation, and writes
engine/teach_planner.py     — teach question planning and feedback-aware queueing
engine/setup_state.py       — persisted onboarding/profile state and provider readiness
engine/security_posture.py  — local machine security recommendation checks
server/main.py              — FastAPI app, lifecycle, bind/TLS startup
server/auth.py              — passphrase login and in-memory session tokens
server/routes/              — query, teach, setup, artifacts, attributes, capture, and session endpoints
server/middleware/          — auth enforcement, interface checks, security headers
server/models/              — Pydantic request/response schemas
frontend/                   — Vite React frontend for Teach, query, graph, and history tabs
scripts/init_db.py          — one-time (idempotent) initialisation script
scripts/capture.py          — quick capture CLI (make capture)
scripts/query.py            — interactive freeform query engine (make query)
scripts/serve.py            — HTTPS server entrypoint (make serve)
scripts/smoke_api.py        — Python API smoke test helper (make smoke)
scripts/view_db.py          — terminal viewer for the identity store (make view)
tests/test_capture.py       — quick capture flow, conflicts, and write-path tests
tests/test_schema.py        — schema and constraint tests
tests/test_llm_router.py    — hardware detection, router resolution, and inference tests
tests/test_query_engine.py  — classifier, retriever, prompts, session, query flow tests
tests/test_server.py        — FastAPI auth, security, CRUD, and capture endpoint tests
tests/test_view_db.py       — viewer output and filtering tests
docs/capture.md             — quick capture command reference
docs/server.md              — FastAPI backend reference
docs/schema.md              — full schema reference
docs/llm_routing.md         — LLM routing reference and key setup guide
docs/query.md               — query engine and interactive session reference
docs/view_db.md             — viewer output format reference
```

See [docs/schema.md](docs/schema.md) for the full schema reference.
See [docs/capture.md](docs/capture.md) for the quick capture reference.
See [docs/llm_routing.md](docs/llm_routing.md) for the LLM routing reference.
See [docs/query.md](docs/query.md) for the query engine reference.
See [docs/server.md](docs/server.md) for the backend server reference.
See [docs/view_db.md](docs/view_db.md) for the viewer reference.
