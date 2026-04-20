# Identity Engine

Identity Engine is a privacy-first personal identity workspace. It stores structured beliefs, preferences, and supporting artifacts in an encrypted local database, then uses LLMs only as controlled reasoning tools behind explicit privacy rules.

Current implementation status, recent changes, and known gaps live in [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md).

## What It Does

- Captures structured identity data such as values, goals, voice traits, and patterns.
- Stores everything locally in a SQLCipher-backed database with append-only history.
- Separates canonical truth from supporting evidence such as uploaded notes and documents.
- Routes all LLM work through a privacy boundary that blocks `local_only` data from leaving the machine.
- Provides a web UI for onboarding, Teach flows, querying, graph review, session history, and settings.

## Architecture

The system is organized around a small set of explicit boundaries:

1. `db/` is the encrypted source of truth.
2. `engine/` handles retrieval, context assembly, privacy enforcement, prompt building, and query/capture workflows.
3. `config/llm_router.py` is the only place allowed to talk to model providers.
4. `server/` exposes a FastAPI API and serves the frontend in production builds.
5. `frontend/` is a React + Vite client for teaching, querying, review, and configuration.

High-level flow:

`user input -> retrieval/context assembly -> privacy broker -> llm router -> response or structured write`

Privacy rules are deterministic, not advisory:

- `local_only` attributes must never be sent to external providers
- external extraction requires explicit consent
- artifacts remain evidence, not canonical identity truth
- updates preserve history instead of overwriting rows in place

## Repository Layout

```text
config/    model routing, provider catalog, runtime settings
db/        SQLCipher connection helpers and schema modules
engine/    core identity, retrieval, capture, query, privacy, and teach logic
server/    FastAPI app, routes, middleware, response schemas
frontend/  React UI and frontend tests
docs/      architecture, privacy, API, and project-state documentation
tests/     backend unit and integration tests
```

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+ and npm
- A working system keyring backend
- Native SQLCipher support if your platform requires it for `sqlcipher3`

Runtime data is created outside the repo under your home directory in `.identity-engine/`.

### 1. Create a virtual environment

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

Backend:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Frontend:

```bash
cd frontend
npm install
cd ..
```

### 3. Initialize the encrypted store

```bash
python scripts/init_db.py
```

This creates the local runtime directory, seeds the database, and stores the encryption key in your system credential store.

### 4. Start the backend

```bash
python scripts/serve.py
```

The API runs on `https://localhost:8443` when Tailscale is not available.

### 5. Start the frontend in a second terminal

Activate the same virtual environment if needed, then run:

```bash
cd frontend
npm run dev
```

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.

### Optional Makefile shortcuts

On systems with GNU Make, the repo also includes convenience commands:

```bash
make setup
make init
make serve
make frontend-install
make frontend-dev
make test
```

## How To Use It

After startup, the main workflows are:

- `Teach`: answer guided questions, add quick notes, and upload local artifacts
- `Query`: ask grounded questions about the stored identity model
- `Graph`: inspect and edit current attributes
- `History`: review session summaries and routing metadata
- `Settings`: manage privacy preferences, providers, and security recommendations

CLI utilities are also available:

- `python scripts/capture.py --text "..."` for quick capture
- `python scripts/query.py` for terminal querying
- `python scripts/view_db.py` for a local store dump
- `python scripts/smoke_api.py --base-url https://127.0.0.1:8443` for an API smoke check

## Development

Backend verification:

```bash
python -m pytest tests -v
python -m pyright
```

Frontend verification:

```bash
cd frontend
npm run test
```

Key docs:

- [docs/architecture.md](docs/architecture.md)
- [docs/privacy-model.md](docs/privacy-model.md)
- [docs/server.md](docs/server.md)
- [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md)
