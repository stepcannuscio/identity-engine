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
- Query Usefulness Tuning + Eval Harness
- Query Feedback Loop
- Voice Fidelity Tuning
- Extraction Consent + Audit Redaction + Artifact Upload Guardrails

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
- can draft or rewrite text in a grounded approximation of the user's voice

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
- raw query text is not stored in routing logs or returned by session APIs

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
- upload handling now rejects oversized requests/files, oversized extracted
  text, malformed DOCX payloads, and DOCX files with oversized
  `word/document.xml`
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
- after onboarding, Teach stays focused on ongoing intake:
  - guided questions
  - quick-note capture
  - tagged file uploads
- setup-heavy panels are shown inside Teach only while onboarding is incomplete
- Teach supports:
  - guided questions
  - quick-note capture
  - tagged file uploads
  - onboarding-time profile/provider/security setup
- onboarding is resumable and skippable; completion state is stored in
  `app_settings`
- Teach question planning is stored explicitly in:
  - `teach_questions`
  - `teach_question_feedback`
- Teach questions are seeded from the canonical interview catalog and then
  refreshed dynamically from coverage gaps, artifact tags, and feedback history
- Teach queue refresh now deduplicates by `intent_key` and normalized prompt
  text, dismissing stale pending duplicates so previously answered or dismissed
  prompts do not reappear
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
- raw-text extraction for capture, interview, and Teach answers now fails
  closed on external backends unless the user explicitly opts in per request

### 19. Profile / Provider / Security Setup Layer
- setup state is stored explicitly, not as a generic blob:
  - `app_settings`
  - `security_check_overrides`
  - `provider_status`
- setup state now persists:
  - `privacy_preference`
  - `active_profile`
  - `preferred_provider`
  - `preferred_backend`
- the web UI now includes a dedicated `Settings` tab for:
  - privacy preference updates
  - model/profile changes
  - provider credential management
  - security recommendation review
- provider setup now uses a shared provider catalog rather than hard-coded cards
- provider metadata now distinguishes:
  - deployment location (`local` vs `external`)
  - trust boundary (`self_hosted` vs third-party external)
  - auth strategy (`none` vs `api_key`)
- the backend now recommends three model/privacy profiles:
  - `private_local_first`
  - `balanced_hybrid`
  - `external_assist`
- onboarding now also captures a privacy preference:
  - `privacy_first`
  - `balanced`
  - `capability_first`
- configuration recommendations are derived from local hardware, the selected
  privacy preference, and currently configured providers
- external provider credentials are stored in the system keychain only and are
  managed through setup routes, not the database
- runtime provider resolution now honors the saved preferred provider when the
  user chooses an external-backed configuration
- setup APIs now expose:
  - `GET /setup/model-options`
  - `POST /setup/providers/{provider}/credentials`
  - `POST /setup/profile`
  - `GET /setup/security-posture`
- unknown security checks can now be manually marked complete by the user from
  Settings without changing the machine-inspected status value
- manual security confirmations are persisted per check code and suppress the
  `update recommended` state for unresolved-but-confirmed items
- macOS security posture checks are read-only and recommendation-first:
  - FileVault
  - personal recovery key availability when detectable
  - immediate password after sleep/screensaver
  - auto-login disabled / login required at boot
- security recommendations now surface the recommended target state and whether
  an update is currently recommended

### 20. Query Usefulness Tuning Layer
- query planning now keeps the public `query_type` stable while extending the
  internal plan with:
  - `intent_tags`
  - `domain_hints`
  - `classification_reason`
- source-profile classification remains deterministic but now uses:
  - normalized tokens
  - ordered phrase rules
  - planning / writing / artifact cues
  - explicit false-positive prevention for generic terms
- identity retrieval scoring now incorporates:
  - normalized label/value/elaboration overlap
  - phrase boosts
  - stronger domain-intent bonuses
  - freshness from `last_confirmed` / `updated_at`
  - correction-aware penalties for unstable labels with prior non-current versions
- preference and artifact selection now also use stronger domain-aware scoring
  so planning and self-model questions are grounded more usefully without
  adding a probabilistic reranker
- planning-oriented preference-sensitive queries now bias toward current goals,
  focus patterns, and stable preference evidence rather than generic retrieval

### 21. Query Calibration + Feedback Layer
- query responses now include privacy-safe `metadata.intent` with:
  - `source_profile`
  - `intent_tags`
  - `domain_hints`
- local-only query usefulness feedback is now persisted through:
  - `POST /query/feedback`
  - `query_feedback`
- feedback labels are:
  - `helpful`
  - `ungrounded`
  - `missed_context`
  - `wrong_focus`
- query feedback is stored separately from canonical attributes and does not
  auto-promote into identity truth
- the repository now includes a versioned deterministic query eval corpus at:
  - `evals/query_usefulness/v1.json`
- the evaluation runner lives at:
  - `python -m engine.query_eval`
- the eval harness exercises:
  - self-reflection grounding
  - planning support
  - artifact lookup behavior
  - thin-context acquisition suggestions
  - external-block detection

### 22. Voice Fidelity Tuning Layer
- query planning now distinguishes explicit voice-imitation drafting from generic
  preference-sensitive work through an internal `voice_generation` source profile
- voice-generation detection remains deterministic and is triggered by explicit
  rewrite/drafting phrasing plus “sound like me” style cues
- context assembly now compiles a bounded `voice_profile` for voice-generation
  requests using:
  - current `voice` domain attributes
  - learned voice-related preference attributes and summaries
  - up to two local exemplar artifact snippets from `voice` artifacts
- prompt building now emits a dedicated `Voice guidance:` block when a
  grounded voice profile is available
- local exemplar snippets are only included for local inference
- external voice-generation requests may still use `external_ok` voice traits,
  but local-only voice traits and exemplar snippets remain blocked or omitted
- local-only voice fidelity feedback is now stored explicitly in:
  - `voice_feedback`
- voice fidelity feedback labels are:
  - `authentic`
  - `not_me`
  - `too_formal`
  - `too_wordy`
  - `wrong_rhythm`
  - `overdone_style`
- negative voice-fidelity feedback also records deterministic low-level
  preference signals in the `voice` category so future drafting can steer away
  from repeated misses without auto-promoting them into canonical identity truth
- the deterministic query eval corpus now includes voice-generation cases that
  verify:
  - voice-profile assembly
  - prompt voice-guidance injection
  - local-only exemplar handling
  - external-safe omission of local exemplar snippets

---

# Key Invariants (DO NOT BREAK)

## Privacy

- `local_only` attributes MUST NEVER leave the system
- system must **fail closed**
- no raw prompts logged
- no raw query text stored in session routing logs or exposed in history APIs/UI
- no raw attribute values in audit logs
- no supporting evidence text exposed via API/UI
- no raw artifact content exposed via API/UI except bounded local prompt context
- external inference must always be explicitly allowed
- external raw-text extraction must require explicit per-request consent
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
- legacy session routing logs are scrubbed to remove stored raw query text
- current attribute states are `active` and `confirmed`
- excluded/non-current states are `rejected`, `superseded`, and `retracted`
- only one current `(domain, label)` may exist at a time
- preference signals are separate from attributes and represent lower-level evidence
- preference promotion must not recreate rejected attributes or overwrite refined values
- onboarding/profile/provider state must live in explicit tables, not a generic
  JSON blob
- query usefulness feedback must remain local-only and separate from canonical
  identity truth
- voice exemplar snippets must remain local-only prompt context
- voice fidelity feedback must remain local-only and separate from canonical
  identity truth

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
- allow external raw-text extraction only after explicit per-request consent for
  capture, interview, and Teach-answer flows
- show privacy-safe session history without raw query text
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
- persist the selected privacy preference, configuration, and provider choice
- use the saved configuration to hydrate the frontend's default query backend
- use the saved preferred provider for external routing across query and Teach flows
- collect structured feedback on Teach questions so irrelevant or duplicate
  prompts are downranked over time
- parse tagged local `.pdf` and `.docx` uploads without introducing OCR or an
  external document service
- inspect and surface macOS security recommendations without blocking the user
- classify self, planning, writing, and artifact-reference queries more precisely
  without changing the public query API
- expose privacy-safe query intent metadata for UI and local feedback workflows
- rank identity evidence with recency, trust, and label-stability signals
- store local-only answer usefulness feedback for future calibration and review
- run a deterministic query usefulness eval corpus without calling an LLM
- distinguish explicit “write in my voice” requests from generic preference-sensitive drafting
- compile bounded voice guidance from structured traits, learned preferences,
  and local writing exemplars
- include local writing exemplar snippets only for local voice-generation runs
- collect local-only voice fidelity feedback and convert repeated misses into
  lower-level voice preference signals without mutating canonical attributes
