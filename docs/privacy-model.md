# Privacy Model

## Core Principle

The LLM is NOT trusted with raw personal data.

## Data Classes

Tier 0: Public
Tier 1: Personal
Tier 2: Sensitive

## Rules

- Default = local_only
- External requires explicit permission
- All outbound data is minimized

## Enforcement Points

- Retrieval layer
- Prompt builder
- LLM router
- Privacy broker and API response normalization

## Failure Mode

System must fail CLOSED:
If unsure → do not send data

## Audit

Every external call must record:
- attributes used
- provider
- reason

The frontend only receives high-level privacy state summaries such as
`local`, `external`, `blocked`, or `unknown`. It does not receive raw prompts,
raw evidence text, or internal audit reason strings.
