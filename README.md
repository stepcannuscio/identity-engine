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
- The interview script uses Ollama (local) for extraction — no external API calls

## Quick start

```sh
make setup      # create venv, install deps, install pre-commit hooks
make init       # generate key, create database, seed domains
make test       # run the test suite
make interview  # start the interactive identity interview
```

## Seeding your identity store

`make interview` launches a guided terminal interview across eight identity
domains: personality, values, goals, patterns, voice, relationships, fears,
and beliefs.

**How it works:**

1. You answer one question at a time in plain English
2. Ollama (`llama3.1:8b`, running locally) extracts structured attributes from
   your answer and shows you a numbered preview
3. You confirm, skip, edit, or retry before anything is written
4. Confirmed attributes are written to the database immediately — nothing is
   batched or written without your explicit approval
5. A `reflection_sessions` record is saved at the end of every session,
   including interrupted ones

**Before your first interview:**

```sh
make init       # if you haven't already
make interview  # Ollama is started and llama3.1:8b pulled automatically
```

You can run the interview as many times as you like. Re-answering a question
whose label already exists prompts you to update (supersede) the old value or
skip — the full history is preserved in `attribute_history`.

## Structure

```
config/settings.py          — paths, keychain access, routing constants
db/connection.py            — SQLCipher connection context manager
db/schema.py                — DDL and domain seeding
scripts/init_db.py          — one-time (idempotent) initialisation script
scripts/seed_interview.py   — interactive identity interview (make interview)
tests/test_schema.py        — schema and constraint tests
tests/test_interview.py     — interview logic, DB helpers, and UI flow tests
docs/schema.md              — full schema reference
docs/interview.md           — interview script reference
```

See [docs/schema.md](docs/schema.md) for the full schema reference.
See [docs/interview.md](docs/interview.md) for the interview script reference.
