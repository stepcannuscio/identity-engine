# FastAPI Server Reference

`make serve` starts the Phase 3 HTTPS backend for the web UI.

## Startup

On startup the server:

1. Detects a Tailscale bind IP from `tailscale0` or `utun*` when available
2. Falls back to `IDENTITY_ENGINE_BIND_IP`, then `127.0.0.1`
3. Refuses to bind to `0.0.0.0`
4. Generates self-signed TLS material in `~/.identity-engine/certs/` on first run
5. Opens request-scoped SQLCipher connections through `db/connection.py`
6. Resolves the LLM backend through `config/llm_router.py`
7. Ensures a UI passphrase exists in the system keychain

The server only serves HTTPS on port `8443`.

When `frontend/dist/` exists, `scripts/serve.py` mounts the built React app at
`/` so the backend serves both static frontend assets and API routes.

## Authentication

All routes except `POST /auth/login` and `GET /health` require a valid session
token.

- Passphrase storage: system keychain service `identity-engine`, username `ui-passphrase`
- Session storage: in memory only
- Token lifetime: 8 hours
- Concurrent sessions: max 5
- Login rate limit: 5 failed attempts per 15 minutes per IP

Supported token headers:

- `Authorization: Bearer <token>`
- `X-Session-Token: <token>`

## Endpoints

### Health

- `GET /health`

### Auth

- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/status`

### Query

- `POST /query`
- `POST /query/stream` — SSE stream with `token`, `metadata`, `warning`, `error`, and `done` events
  - query failures now distinguish privacy routing blocks, missing backend configuration,
    and upstream provider failures
- `POST /query/feedback` — stores local-only answer usefulness feedback for calibration

### Attributes

- `GET /attributes`
- `GET /attributes/{id}`
- `POST /attributes`
- `PUT /attributes/{id}`
- `DELETE /attributes/{id}`
- `POST /attributes/{id}/confirm`
- `GET /domains`

### Capture

- `POST /capture/preview`
- `POST /capture`
  - accepts the raw quick-capture text for non-interactive extraction
  - also accepts an optional `accepted` array of user-approved preview items
  - accepts optional `allow_external_extraction`
  - when the resolved backend is external and raw extraction would occur, the
    request fails closed with `409 external_extraction_consent_required` unless
    consent is explicitly provided

### Interview / Teach

- `POST /interview/preview`
- `POST /interview`
- `POST /teach/questions/{id}/answer`
  - interview and Teach answer extraction follow the same external-consent rule
    as capture
  - accepted preview items can still be saved without fresh extraction

### Sessions

- `GET /sessions` — includes stored `routing_log` entries used by the History tab
  - routing log entries contain privacy-safe metadata only; raw query text is
    not stored or returned
- `GET /sessions/{id}`
- `GET /sessions/current`

### Artifacts

- `POST /artifacts`
  - accepts JSON text or tagged `.txt`, `.md`, `.pdf`, and `.docx` uploads
  - enforces conservative request/file/text limits
  - rejects malformed or oversized DOCX payloads before expensive parsing

## Security middleware

Every response includes:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Strict-Transport-Security: max-age=31536000`
- `Content-Security-Policy: default-src 'self'`

Access logs are written to `~/.identity-engine/access.log` without request
bodies or auth tokens.

Database connections are request-scoped rather than shared across the process.
For streaming query responses, identity retrieval happens before SSE streaming
begins so the database connection closes immediately.

Query error logs and session history also avoid storing raw query text.
