# Query Engine Reference

`make query` starts an interactive freeform query session backed by the
identity store.

Phase 3 also exposes the same query engine over the FastAPI backend:

- `POST /query` for full JSON responses
- `POST /query/stream` for SSE token streaming

## End-to-end flow

For each user question:

1. Classify query as `simple` or `open_ended` (`engine/query_classifier.py`)
2. Retrieve and score active attributes (`engine/retriever.py`)
3. Build grounded prompt with identity context and capped history (`engine/prompt_builder.py`)
4. Generate response through configured backend (`config/llm_router.py`)
5. Update in-memory session state (`engine/session.py`)

## Retrieval budgets

- `simple`: max 8 attributes, max 2 domains, score threshold 0.3
- `open_ended`: max 20 attributes, max 8 domains, score threshold 0.15
- Explicit domain-intent queries (for example, goals/values/personality) use a
  fallback that injects top attributes from the requested domain(s) even when
  lexical overlap is weak.

## Safety constraints

- `retriever.py`, `query_classifier.py`, and `prompt_builder.py` perform no LLM calls
- Only `query_engine.py` calls inference (`llm_router.generate_response`)
- Prompt builder enforces routing: `local_only` attributes cannot be included for external backends (`RoutingViolationError`)
- Session history is in-memory only during runtime

## Session commands

Inside `make query`:

- `history` prints retained history
- `clear` clears history
- `status` shows query count, retrieved-attribute total, backend
- `quit` / `q` exits cleanly

On exit, one `reflection_sessions` row is written with summary metadata and a
routing log.
