# Architecture Overview

## System Summary

A local-first identity system with controlled LLM augmentation.

## High-Level Flow

User Input / Artifact Upload → Query Engine / Capture Flow / Artifact Ingestion →
Retrieval / Extraction Prep → Prompt Builder → Privacy Broker → LLM Router → Response

## Core Components

### Identity Store
- SQLCipher encrypted DB
- Stores structured attributes
- Stores local artifacts and artifact chunks
- Source of truth

### Retrieval Engine
- Selects relevant attributes
- Applies scoring and thresholds
- Can retrieve bounded artifact chunks with deterministic keyword matching

### Context Assembler
- Builds structured query context
- Augments general retrieval with bounded learned-preference context
- Falls back to bounded artifact evidence for deeper or under-specified queries
- Keeps preference selection deterministic and task-sensitive

### Prompt Builder
- Builds grounded prompts
- Adds concise learned-preference guidance when relevant
- Adds bounded local artifact excerpts only for local inference
- Enforces routing constraints

### Privacy Broker
- Centralizes application-level inference decisions
- Makes local vs external inference explicit
- Enforces query routing before delegating to the router
- Feeds privacy-safe execution summaries to the API so the frontend can show
  local, external, blocked, or unavailable states without exposing raw prompts
  or audit payloads

### LLM Router
- Handles model selection
- Local-first fallback chain

### Query Engine
- Orchestrates entire flow

### Artifact Ingestion Layer
- Persists raw uploaded text locally
- Chunks artifacts into ordered evidence units
- Keeps artifact storage separate from canonical attributes

### Preference Runtime Layer
- Selects relevant confirmed or inferred preference attributes
- Adds privacy-safe summarized preference tendencies when helpful
- Supports lightweight deterministic ranking for future planning and recommendation flows

## Trust Boundaries

### Trusted (Local)
- Database
- Retrieval
- Prompt builder
- Privacy broker
- Artifact ingestion and chunk retrieval

### Semi-trusted
- Local LLM (Ollama)

### Untrusted
- External APIs

## Key Constraint

Raw identity data must never leave the system unless explicitly allowed.

Artifacts are treated as local-only evidence. Full artifact bodies are stored
locally, while prompt context only ever receives a small bounded set of
retrieved chunks for local inference.

`config/llm_router.py` remains the low-level unified inference utility; the
application now reaches it through `engine/privacy_broker.py`.
