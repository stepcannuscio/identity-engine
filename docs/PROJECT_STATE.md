# Identity Engine — Project State

## Purpose

This document captures the current system state after completing:

- Privacy Broker
- Context Assembly
- Inference Audit
- Inference Evidence Helpers
- Frontend Privacy States
- Provenance Read API
- Attribute Correction Loop
- Preference Learning
- Preference Promotion
- Artifact Ingestion and Retrieval
- Coverage and Answer Confidence
- Targeted Data Acquisition
- Unified Onboarding + Teach Flow
- Profile-Based Model Setup
- Machine Security Recommendations

It is intended to:
- allow seamless continuation in a new chat
- provide Codex with full architectural context
- preserve invariants and design decisions
- define the next phase of development

---

# System Overview

The Identity Engine is a **privacy-first, local-first identity modeling system** that:

- stores identity as structured, evolving attributes
- enforces strict boundaries between local and external inference
- tracks inference decisions (audit)
- tracks why beliefs exist (provenance)
- surfaces privacy behavior to the user
- guides first-run onboarding through the web UI
- provides an ongoing `Teach` workflow for structured and unstructured intake
- allows users to confirm, reject, and refine beliefs
- stores lightweight local preference signals for future planning/recommendation use
- stores local artifacts as retrievable evidence without turning them into source-of-truth attributes
- recommends privacy/model configurations and local machine security posture

---

# Completed Architecture

## Core Layers

### 1. Identity Store
- SQLCipher encrypted
- canonical truth layer
- append-only history

### 2. Retrieval Engine
- relevance scoring
- domain-aware selection

### 3. Context Assembler
- structured context construction
- separates retrieval from prompt building

### 4. Privacy Broker
- central inference boundary
- enforces routing rules
- blocks unsafe external inference

### 5. Prompt Builder
- formatting only
- no business logic

### 6. LLM Router
- local-first execution
- external fallback

### 7. Inference Audit Layer
- records inference decisions
- stored in session routing logs
- privacy-safe metadata only

### 8. Inference Evidence Layer
- tracks provenance for inferred attributes
- supports multiple evidence entries
- local-only, sensitive

### 9. Provenance API ✅
- `GET /attributes/{id}/provenance`
- returns summarized evidence
- never exposes raw supporting text

### 10. Frontend Privacy States
- shows:
  - Local
  - External
  - Blocked
- improves user trust

### 11. Attribute Correction Loop
- `PATCH /attributes/{id}` supports:
  - `confirm`
  - `reject`
  - `refine`
- all correction actions write `attribute_history`
- confirmation marks attributes as higher-trust current beliefs
- rejection excludes attributes from retrieval and current listings
- refinement supersedes the old version and creates a new current row
- frontend graph view includes light confirm/reject controls

### 12. Preference Signal Layer
- `POST /preferences/signals` stores explicit local preference feedback
- `GET /preferences/signals` lists raw signals with optional filters
- `GET /preferences/signals/summary` provides deterministic grouped summaries
- preference signals are stored separately from canonical attributes
- raw signal history remains local data and is not routed through audit/privacy summaries

### 13. Preference Promotion Loop
- `POST /preferences/promote` runs deterministic local promotion manually
- repeated stable preference signals can become inferred attributes
- promotion uses simple thresholds and conflict checks, not probabilistic scoring
- promoted attributes default to `local_only` and attach summarized local evidence
- promotion respects user corrections by not recreating recently rejected matches
- rerunning promotion refreshes existing inferred attributes instead of duplicating them

### 14. Coverage & Answer Confidence Layer
- deterministic evaluator inspects the assembled context before inference runs
- scores context on a 100-point style model: attribute score (cap 50) weighted
  by status and per-attribute confidence; preference score (cap 25) tiered by
  attribute type and signal cluster strength; artifact score (cap 20) scored by
  source diversity; consistency adjustment (±5)
- uses internal source profiles for scoring and guardrails:
  - `self_question` (high ≥ 70)
  - `evidence_based` (high ≥ 60)
  - `preference_sensitive` (high ≥ 60)
  - `general` (high ≥ 65)
- enforces structural guardrails:
  - no high confidence without identity support for self-questions and general queries
  - evidence-based queries can lean harder on artifacts, but single-source
    artifact evidence stays below high confidence unless structured support is present
- exposes a ScoreBreakdown dataclass for testing and calibration
- low and medium confidence append a brief hedge to the system prompt so the
  model acknowledges limitations
- `insufficient_data` short-circuits the LLM call and returns a canned message
  suggesting next steps (unless privacy routing would otherwise force a
  `blocked` decision — in that case the privacy broker still fires)
- classification and counts are surfaced on query responses as
  `metadata.confidence` and `metadata.coverage`

### 15. Artifact Ingestion Layer
- `POST /artifacts` accepts JSON text plus tagged `.txt`, `.md`, `.pdf`, and
  `.docx` uploads
- artifacts are stored locally with raw content plus ordered chunks
- upload tags are normalized into `artifact_tags` and can influence Teach
  planning and retrieval
- chunk retrieval is deterministic keyword matching, not embeddings
- query context can blend artifact evidence with structured identity and
  preference signals instead of only using artifacts as a thin-coverage fallback
- artifact evidence is prompt-bounded, treated as local-only context, and
  ranked below canonical identity for self-questions

### 16. Dynamic Source Weighting Layer
- query planning now keeps public `query_type` as `simple|open_ended` while
  adding an internal `source_profile`
- source profiles are:
  - `self_question`
  - `evidence_based`
  - `preference_sensitive`
  - `general`
- context assembly now gathers scored candidates from identity attributes,
  learned preferences, and artifacts before doing final selection
- final prompt grounding uses a blended ranked evidence list with explicit
  source labels instead of separate identity/preference/artifact sections
- selection is deterministic and uses:
  - per-source normalization
  - source weights by profile
  - trust bonuses for confirmed or active structured signals
  - domain/profile bonuses
  - artifact diversity bonuses
  - duplicate-artifact penalties
- artifacts remain supporting evidence only; they never become canonical truth

### 17. Targeted Data Acquisition Layer
- deterministic acquisition planning now runs after context assembly and
  coverage scoring, before any query LLM call
- acquisition planning uses coverage gaps plus source profile requirements to
  identify:
  - missing identity coverage
  - missing preference coverage
  - missing artifact coverage
- query responses now surface structured `metadata.acquisition` with:
  - `status`
  - `gaps`
  - `suggestions`
- suggestions are deterministic, privacy-safe, and capped
- the system now reuses existing intake paths instead of inventing a second
  acquisition pipeline:
  - quick capture for identity notes
  - preference signal capture for preference-sensitive gaps
  - canonical interview questions for thin core-domain coverage
  - artifact upload suggestions for evidence-based gaps
- web API now supports guided interview preview/save with:
  - `POST /interview/preview`
  - `POST /interview`
- the CLI interview and web interview now share one canonical interview domain
  and question catalog
- targeted acquisition remains local planning logic only; it does not add any
  new direct LLM path outside `PrivacyBroker` / `llm_router.py`

### 18. Unified Onboarding + Teach Layer
- the web UI now includes a first-class `Teach` tab that is also the default
  destination after first login until onboarding is complete
- Teach supports:
  - guided questions
  - quick-note capture
  - tagged file uploads
  - profile/provider setup
  - security recommendations
- onboarding is resumable and skippable; completion state is stored in
  `app_settings`
- Teach question planning is stored explicitly in:
  - `teach_questions`
  - `teach_question_feedback`
- Teach questions are seeded from the canonical interview catalog and then
  refreshed dynamically from coverage gaps, artifact tags, and feedback history
- generated questions use a sanitized metadata-only prompt:
  - domain names
  - coverage counts
  - artifact tags
  - feedback counts
- generated questions never send raw attribute values, raw answers, or raw
  artifact content to external providers
- Teach answer extraction reuses interview-style persistence semantics:
  - extracted attributes are preview/save capable
  - writes create or supersede canonical attributes
  - writes preserve audit/history behavior

### 19. Profile / Provider / Security Setup Layer
- setup state is stored explicitly, not as a generic blob:
  - `app_settings`
  - `provider_status`
- the backend now recommends three model/privacy profiles:
  - `private_local_first`
  - `balanced_hybrid`
  - `external_assist`
- recommendations are derived from local hardware plus currently configured
  providers
- external provider credentials are stored in the system keychain only and are
  managed through setup routes, not the database
- setup APIs now expose:
  - `GET /setup/model-options`
  - `POST /setup/providers/{provider}/credentials`
  - `POST /setup/profile`
  - `GET /setup/security-posture`
- macOS security posture checks are read-only and recommendation-first:
  - FileVault
  - personal recovery key availability when detectable
  - immediate password after sleep/screensaver
  - auto-login disabled / login required at boot

---

# Key Invariants (DO NOT BREAK)

## Privacy

- `local_only` attributes MUST NEVER leave the system
- system must **fail closed**
- no raw prompts logged
- no raw attribute values in audit logs
- no supporting evidence text exposed via API/UI
- no raw artifact content exposed via API/UI except bounded local prompt context
- external inference must always be explicitly allowed
- external Teach question generation may only use sanitized metadata and must
  fail closed if sanitization cannot produce an external-safe prompt

## Architecture

- ALL inference flows through `PrivacyBroker`
- ContextAssembler controls data selection
- PromptBuilder controls formatting only
- Router executes inference only
- PrivacyBroker enforces all rules
- Teach question generation still flows through `PrivacyBroker`
- onboarding/profile state must remain server-owned and explicit

## Data Model

- attributes are canonical truth
- artifacts are local evidence, not canonical truth
- history is append-only
- inferred attributes may include evidence
- audit logs describe decisions, not content
- provenance explains *why* a belief exists
- current attribute states are `active` and `confirmed`
- excluded/non-current states are `rejected`, `superseded`, and `retracted`
- only one current `(domain, label)` may exist at a time
- preference signals are separate from attributes and represent lower-level evidence
- preference promotion must not recreate rejected attributes or overwrite refined values
- onboarding/profile/provider state must live in explicit tables, not a generic
  JSON blob

---

# What the System Can Do Now

- store identity securely and locally
- enforce strict privacy boundaries
- construct context-aware identity prompts
- safely use LLMs without leaking sensitive data
- audit inference behavior
- explain why inferred attributes exist
- surface privacy behavior to users
- let users confirm beliefs they trust
- let users reject beliefs they do not want used
- let users refine beliefs without overwriting history
- users can confirm attributes to mark them as trusted current beliefs
- users can reject attributes so they are excluded from retrieval
- users can refine attributes by creating a new version instead of overwriting
- provenance remains attached to the original inferred attribute version
- retrieval favors confirmed attributes and ignores rejected ones
- current listings and domain counts include both `active` and `confirmed`
- record explicit likes, dislikes, accepts, rejects, prefers, and avoids as local preference signals
- summarize preference tendencies without exposing raw signal history
- promote stable preference tendencies into inferred local-only attributes on demand
- use relevant learned preferences during context assembly and prompt grounding
- summarize preference tendencies into bounded runtime guidance instead of dumping signal history
- deterministically score future candidates against learned preferences with transparent weights
- ingest local notes, documents, and uploads into retrievable artifact storage
- blend bounded artifact chunks with identity and preference signals using
  query-specific source weighting
- keep raw artifact bodies local while still grounding local answers in uploaded content
- assess whether enough grounded context exists to answer a query before calling the LLM
- explicitly acknowledge partial or low coverage in prompts instead of generating generic answers
- skip LLM calls and return a helpful explanation when no relevant context is available
- rank grounded prompt context across sources so self-questions favor identity,
  evidence-based questions favor artifacts, and drafting/planning questions
  favor learned preferences
- suggest the smallest next piece of data to collect when coverage is thin
- surface deterministic follow-up actions in query metadata for:
  - quick identity capture
  - quick preference capture
  - guided interview questions
  - artifact upload
- answer guided interview questions from the web UI using preview/save flows
  that preserve interview write semantics and audit trail rules
- guide first-run onboarding through the Teach tab instead of relying on the
  terminal interview as the primary UX
- recommend local/external model profiles based on hardware and configured providers
- persist the selected profile and use it to hydrate the frontend's default query backend
- collect structured feedback on Teach questions so irrelevant or duplicate
  prompts are downranked over time
- parse tagged local `.pdf` and `.docx` uploads without introducing OCR or an
  external document service
- inspect and surface macOS security recommendations without blocking the user
