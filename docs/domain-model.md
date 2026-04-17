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

## Artifacts

Artifacts are local evidence objects such as:
- notes
- journals
- transcripts
- uploaded documents

Artifacts are not canonical identity beliefs. They store raw local content plus
ordered chunks for retrieval. An artifact may optionally be associated with an
identity domain, but it remains evidence rather than truth.

## Artifact Chunks

Artifact chunks are bounded slices of artifact content used for retrieval.

They are:
- stored locally
- ordered
- retrievable by deterministic keyword matching
- eligible for local prompt grounding only in bounded form

They are not exposed through privacy summaries, audit logs, or provenance APIs
as raw text.

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

## Coverage and Confidence

Answer confidence is a *runtime* signal, not stored state. The coverage
evaluator derives it from the assembled context on every query using a
deterministic 100-point style model:

- **Attribute score** (cap 50): confirmed attributes score highest (12 pts),
  then explicit active (10), then inferred (6), with per-attribute confidence
  modifiers (+2 / +1 / −2) that reward high-quality beliefs and penalise
  low-confidence ones
- **Preference score** (cap 25): confirmed preference attributes score 8 pts,
  active 5 pts; signal clusters from the preference runtime layer contribute
  4 pts (strong) or 2 pts (weak), with a 3-pt bonus for negative signals that
  confirm what to avoid
- **Artifact score** (cap 20): scored by source diversity — first chunk per
  source is worth 4 pts; second and third distinct sources each add a bonus;
  multi-source agreement adds 3 pts; a single weak chunk is penalised
- **Consistency adjustment** (±5): +5 when all three evidence types contribute;
  −5 when there is a strong split in preference signals

Classification bands (global defaults; per-profile overrides for narrow
preference, recommendation, broad self-model, and artifact-grounded queries):

- `high_confidence`: score ≥ 65
- `medium_confidence`: 45–64
- `low_confidence`: 25–44
- `insufficient_data`: score < 25 or no signals of any kind

Structural guardrails prevent artifacts from overriding identity absence:
no query can reach `high_confidence` without at least one active attribute
or confirmed preference attribute, and artifact-only single-source evidence
is capped below `medium_confidence`.

The label is attached to the query response metadata so clients can render it,
and drives how the prompt hedges or whether the LLM is called at all.
Broad self-model questions require stronger evidence (high ≥ 70) than narrow
preference questions (high ≥ 55), reflecting the system's preference for
honesty over overconfident answers.

## Key Idea

Identity is composed of small, testable beliefs — not large summaries.
Artifacts support those beliefs as evidence without replacing them.
