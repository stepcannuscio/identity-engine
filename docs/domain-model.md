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

## Preference Signals

Preference signals are separate from attributes.

They represent small local observations such as:
- a user preferring concise responses
- a user liking a category of recommendation
- a user avoiding a certain style or option

Signals are not canonical identity beliefs on their own.
They act as local evidence that future recommendation, planning, or inference
features may consult when deciding whether to promote a pattern into an
attribute.

At runtime, signals may also be condensed into small deterministic preference
summaries for local context assembly. Raw signal history is not prompt context.

## Runtime Preference Summaries

Runtime preference summaries are:
- bounded
- task-sensitive
- local-first
- explainable

They combine:
- current preference attributes when relevant
- summarized positive or avoid tendencies from signal aggregates when useful

They do not replace attributes as the source of truth. They are a lightweight
runtime layer used to personalize prompt context and future deterministic
ranking.

## Key Idea

Identity is composed of small, testable beliefs — not large summaries.
