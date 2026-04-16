# AGENTS.md

## Project Purpose
This repository implements a **privacy-first personal identity engine**.

The system:
- Models a user's identity (values, goals, preferences, voice, patterns)
- Stores all data locally in an encrypted SQLCipher database
- Uses LLMs only as reasoning tools — never as the source of truth

## Core Principles
- Local-first architecture
- User-owned data
- Explicit consent for any external data sharing
- Structured identity > raw text blobs
- Deterministic privacy enforcement > LLM judgment

## Critical Rules

### LLM Usage
- ALL LLM calls MUST go through `llm_router.py`
- No direct API calls elsewhere in the codebase
- Local models are always preferred
- External calls must respect attribute `routing`

### Privacy
- `local_only` attributes MUST NEVER be sent externally
- `external_ok` attributes may be used externally
- Violations must raise errors (fail closed)

### Data Integrity
- Only one active `(domain, label)` allowed
- Updates must create history entries
- Never overwrite without audit trail

### Architecture Boundaries
Core components:
- Identity Store (DB)
- Evidence (future)
- Retrieval Engine
- Prompt Builder
- LLM Router
- Query Engine

## Coding Guidelines
- Small, composable modules
- No business logic in routes
- Explicit interfaces over implicit coupling
- Prefer clarity over cleverness

## Testing Requirements
Every feature must include:
- Unit tests
- Routing/privacy tests
- Failure case tests

## When Unsure
Choose:
- More privacy
- Less data sent
- More explicit control