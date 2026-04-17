# Architecture Overview

## System Summary

A local-first identity system with controlled LLM augmentation.

## High-Level Flow

User Input → Query Engine / Capture Flow → Retrieval / Extraction Prep → Prompt Builder → Privacy Broker → LLM Router → Response

## Core Components

### Identity Store
- SQLCipher encrypted DB
- Stores structured attributes
- Source of truth

### Retrieval Engine
- Selects relevant attributes
- Applies scoring and thresholds

### Context Assembler
- Builds structured query context
- Augments general retrieval with bounded learned-preference context
- Keeps preference selection deterministic and task-sensitive

### Prompt Builder
- Builds grounded prompts
- Adds concise learned-preference guidance when relevant
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

### Semi-trusted
- Local LLM (Ollama)

### Untrusted
- External APIs

## Key Constraint

Raw identity data must never leave the system unless explicitly allowed.

`config/llm_router.py` remains the low-level unified inference utility; the
application now reaches it through `engine/privacy_broker.py`.
