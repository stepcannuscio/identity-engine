# identity-engine

A privacy-first personal identity store. Models who you are — values, goals,
personality, patterns — in a structured, queryable, encrypted local database.

## Status: Phase 1 — Foundation

Schema and security infrastructure only. No application layer yet.

## Security model

- All data lives in `~/.identity-engine/identity.db` (never inside this repo)
- The database is encrypted with SQLCipher (AES-256)
- The encryption key is stored in the system keychain — never on disk in plaintext
- Pre-commit hooks block any attempt to commit `.db` files or secret-like strings
- Each attribute carries a `routing` flag: `local_only` (default) or `external_ok`

## Quick start

```sh
make setup   # create venv, install deps, install pre-commit hooks
make init    # generate key, create database, seed domains
make test    # run the test suite
```

## Structure

```
config/settings.py      — paths, keychain access, routing constants
db/connection.py        — SQLCipher connection context manager
db/schema.py            — DDL and domain seeding
scripts/init_db.py      — one-time (idempotent) initialisation script
tests/test_schema.py    — schema and constraint tests
docs/schema.md          — full schema documentation
```

See [docs/schema.md](docs/schema.md) for the full schema reference.
