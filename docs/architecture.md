# Architecture Overview

## System Summary

A local-first identity system with controlled LLM augmentation.

## High-Level Flow

User Input → Query Engine → Retrieval → Prompt Builder → LLM Router → Response

## Core Components

### Identity Store
- SQLCipher encrypted DB
- Stores structured attributes
- Source of truth

### Retrieval Engine
- Selects relevant attributes
- Applies scoring and thresholds

### Prompt Builder
- Builds grounded prompts
- Enforces routing constraints

### LLM Router
- Handles model selection
- Local-first fallback chain

### Query Engine
- Orchestrates entire flow

## Trust Boundaries

### Trusted (Local)
- Database
- Retrieval
- Prompt builder

### Semi-trusted
- Local LLM (Ollama)

### Untrusted
- External APIs

## Key Constraint

Raw identity data must never leave the system unless explicitly allowed.