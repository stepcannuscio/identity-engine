# Domain Model

## Core Entity: Attribute

Each attribute represents a single identity fact.

Fields:
- label
- value
- domain
- confidence
- source
- routing
- status

## Domains
- personality
- values
- goals
- patterns
- voice
- relationships
- fears
- beliefs

## Routing

- local_only → cannot leave system
- external_ok → can be sent to APIs

## Sessions
Reflection sessions track:
- queries
- routing decisions
- summary

## Inference Evidence
Links inferred attributes to supporting data.
Stored through a dedicated helper layer so evidence writes stay explicit and
consistent.
`supporting_text` is provenance data for controlled evidence reads and review,
not general prompt/context output.

## Key Idea

Identity is composed of small, testable beliefs — not large summaries.
