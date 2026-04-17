# Query Engine Reference

`make query` starts an interactive freeform query session backed by the
identity store.

Phase 3 also exposes the same query engine over the FastAPI backend:

- `POST /query` for full JSON responses
- `POST /query/stream` for SSE token streaming

## End-to-end flow

For each user question:

1. Classify query as public `simple|open_ended` plus an internal `source_profile` (`engine/query_classifier.py`)
2. Gather scored candidates from identity attributes, learned preferences, and artifacts (`engine/retriever.py`, `engine/preference_summary.py`, `engine/artifact_retrieval.py`)
3. Merge and rank those candidates deterministically in the context assembler (`engine/context_assembler.py`)
4. Build a blended grounded prompt with explicit source labels and capped history (`engine/prompt_builder.py`)
5. Route application-level inference through `engine/privacy_broker.py`
6. Delegate the approved request to the configured backend (`config/llm_router.py`)
7. Update in-memory session state (`engine/session.py`)

## Retrieval budgets

- `simple`: max 8 attributes, max 2 domains, score threshold 0.3
- `open_ended`: max 20 attributes, max 8 domains, score threshold 0.15
- Explicit domain-intent queries (for example, goals/values/personality) use a
  fallback that injects top attributes from the requested domain(s) even when
  lexical overlap is weak.

## Source Profiles

Internal source profiles drive blending and confidence behavior without changing
the public `query_type` field:

- `self_question`: favor canonical identity attributes
- `evidence_based`: favor artifact evidence while still checking structured support
- `preference_sensitive`: favor learned preferences for drafting, planning, or selection work
- `general`: balanced default

The final prompt uses a single ranked `Grounded context:` block. Items are
labeled as `[identity]`, `[preference]`, or `[artifact]`. Artifacts are always
supporting evidence rather than canonical truth.

## Safety constraints

- `retriever.py`, `query_classifier.py`, and `prompt_builder.py` perform no LLM calls
- Query inference flows through `PrivacyBroker`, which centralizes application-level routing checks before calling `llm_router.py`
- Prompt builder still retains a fail-closed guard: `local_only` attributes cannot be included for external backends (`RoutingViolationError`)
- Selected artifact evidence is always treated as local-only context
- Session history is in-memory only during runtime

## Session commands

Inside `make query`:

- `history` prints retained history
- `clear` clears history
- `status` shows query count, retrieved-attribute total, backend
- `quit` / `q` exits cleanly

On exit, one `reflection_sessions` row is written with summary metadata and a
routing log.
