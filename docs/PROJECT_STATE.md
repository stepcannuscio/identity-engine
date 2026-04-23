# Identity Engine — Project State

## Purpose

This is the canonical project-state document for the repository. The README is intentionally product- and architecture-focused; implementation status, open gaps, and continuation context belong here.

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
- Semantic Retrieval Bridge
- Query Feedback Loop
- Feedback Recalibration Loop
- Voice Fidelity Tuning
- Extraction Consent + Audit Redaction + Artifact Upload Guardrails
- Frontend Route Code-Splitting
- Generalized Evidence Layer
- Passive Session Learning Staging
- Conversation Signal Review
- Cross-Domain Synthesis Staging
- Cross-Domain Synthesis Accept-Dismiss + Narrative Generation
- Temporal Intelligence Analysis
- Natural Voice Learning
- Deep Reflection Mode

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
- `local_only` identity attributes, preferences, and artifacts are stripped
  from the outgoing attribute list before the broker is called for external backends;
  the broker is a last-line-of-defense fail-closed check, not the primary strip point
- audit trail records `contains_local_only_context` (data was present) and
  `local_only_stripped_for_external` (data was silently removed) separately

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
  suggesting next steps (unless the stripped attribute list still contains
  `local_only` items — in that case the privacy broker still fires)
- classification and counts are surfaced on query responses as
  `metadata.confidence` and `metadata.coverage`

### 15. Artifact Ingestion Layer
- `POST /artifacts` accepts JSON text plus tagged `.txt`, `.md`, `.pdf`, and
  `.docx` uploads
- artifacts are stored locally with raw content plus ordered chunks
- artifact metadata can now persist local-only analysis output:
  - `content_kind`
  - retrieval descriptor tokens
  - short local summary
  - reviewable candidate attributes
  - reviewable candidate preference signals
- `POST /artifacts/{artifact_id}/analyze` runs local-only artifact analysis and
  returns reviewable candidates without promoting them automatically
- `POST /artifacts/{artifact_id}/promote` accepts selected candidates and
  promotes them into canonical attributes or preference signals
- upload handling now rejects oversized requests/files, oversized extracted
  text, malformed DOCX payloads, and DOCX files with oversized
  `word/document.xml`
- upload tags are normalized into `artifact_tags` and can influence Teach
  planning and retrieval
- chunk retrieval remains deterministic keyword matching, but now also scores:
  - tags
  - filenames
  - persisted artifact-analysis descriptors
  - list-like artifact structure for inventory-style questions
- query context can blend artifact evidence with structured identity and
  preference signals instead of only using artifacts as a thin-coverage fallback
- artifact evidence is prompt-bounded, treated as local-only context, and
  may now answer artifact-grounded self-style questions when canonical identity
  support is absent

### 16. Dynamic Source Weighting Layer
- query planning now keeps public `query_type` as `simple|open_ended` while
  adding an internal `source_profile`
- source profiles are:
  - `self_question`
  - `artifact_grounded_self`
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
- when a self-style query only has upload evidence, the engine can reroute to
  `artifact_grounded_self` and answer from observed upload evidence instead of
  pretending the upload does not exist
- artifact-grounded answers are instructed to use observed wording rather than
  converting upload-only evidence into stable identity or preference claims
- if an external backend is active but the best evidence is local-only upload
  content, query execution now falls back to a local model when available;
  otherwise it returns an explicit artifact-aware privacy message

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
  - local upload analysis and review
  - explicit promotion of reviewed upload insights into canonical stores
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
- Teach uploads now keep the artifact boundary intact while exposing an
  `upload -> analyze -> promote` workflow:
  - upload first stores searchable local evidence
  - local analysis proposes candidate facts and preferences
  - only explicitly promoted candidates become canonical truth

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
  - a separate deterministic concept-expansion component for abstract queries
  - phrase boosts
  - stronger domain-intent bonuses
  - freshness from `last_confirmed` / `updated_at`
  - correction-aware penalties for unstable labels with prior non-current versions
- deterministic concept expansion is now domain-aware and can bridge abstract
  prompts such as “what motivates me?” to labels like `intrinsic_drive`
- retrieval now also supports an optional bounded similarity tier:
  - temporary SQLite FTS5 matching over active attribute text
  - local/private embedding similarity via Ollama `nomic-embed-text` when that
    embedding model is already available
  - similarity bonus is capped and cannot override the main deterministic score
- attribute embedding vectors are cached locally in:
  - `attribute_embedding_cache`
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
- deterministic retrieval calibration is now derived from accumulated
  `query_feedback` rows and persisted in:
  - `retrieval_calibration`
- calibration is computed conservatively per `(domain, source_profile,
  feedback_pattern)` and only applies bounded domain-level score deltas
- retrieval now loads calibration deltas for the active source profile and
  applies them as a capped adjustment during attribute scoring; calibration can
  nudge ranking but cannot override the main deterministic relevance signal
- query feedback rows can now persist the retrieved attribute ids for the
  grounded answer, still as local-only feedback metadata
- repeated low-rated feedback linked to the same inferred attribute now writes
  an append-only `attribute_history` entry and lowers attribute confidence
  conservatively without mutating user-authored values
- query feedback writes now trigger a background-safe recalibration pass after
  each 10 new feedback records
- low-confidence coverage notes can now surface recent repeated
  `missed_context` patterns for the same domain/profile so the UI makes known
  grounding gaps visible instead of treating them as generic thin context
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

### 23. Frontend Route Code-Splitting Layer
- the web app now lazy-loads top-level authenticated tabs:
  - Teach
  - Settings
  - Query
  - Graph
  - History
- auth/session restore, shell chrome, and bootstrap state remain eagerly loaded
  so first paint stays predictable
- route transitions now render through lightweight loading boundaries using the
  same workspace-loading language already present in the app
- Vite production builds now use explicit chunking for:
  - React/runtime
  - routing/state
  - markdown/rendering
  - per-route tab bundles
- the previous large production chunk warning has been resolved

### 24. Generalized Evidence Layer
- the system now includes a standalone generalized evidence index backed by:
  - `evidence_records`
  - `evidence_links`
- this layer stores privacy-safe summaries and links only; it does not copy raw
  artifact bodies, raw supporting evidence text, or raw query/response text
- artifacts, inference evidence, query usefulness feedback, and voice fidelity
  feedback now register generalized evidence records as part of their normal
  write paths
- existing source tables remain the source of detail:
  - `artifacts`
  - `inference_evidence`
  - `query_feedback`
  - `voice_feedback`
- schema initialization now backfills generalized evidence records idempotently
  for pre-existing rows
- the backend now exposes:
  - `GET /evidence`
- `GET /attributes/{id}/provenance` remains stable, but now reads through the
  generalized evidence layer when generalized provenance entries are available

### 25. Passive Session Learning Staging
- completed query turns can now trigger a best-effort passive learning pass
  after the exchange is recorded in session state
- this flow is implemented in `engine/session_learner.py`
- gating is conservative:
  - only runs when the user message has at least 20 words
  - skips `high_confidence` turns to reduce noise
  - prefers a local Ollama model and silently skips when no local model is ready
- all inference still flows through `PrivacyBroker`; no direct model calls were
  added
- the learner stages review-only conversation signals in
  `extracted_session_signals`; it does not promote anything directly into
  canonical identity truth
- staged signal types currently include:
  - `attribute_candidate`
  - `preference`
  - `correction`
- staged payloads include lightweight source metadata such as:
  - `source_profile`
  - `domain_hints`
  - a bounded `query_excerpt`
  - linked attribute ids for correction candidates when available
- extraction failures are intentionally non-blocking and do not affect the
  user-facing query response path

### 26. Conversation Signal Review
- Teach can now surface reviewable passive-learning items from recent query
  sessions through:
  - `GET /teach/conversation-signals`
  - `POST /teach/conversation-signals/{signal_id}/accept`
  - `POST /teach/conversation-signals/{signal_id}/dismiss`
- Teach bootstrap now includes a `conversation_signal` card when staged items
  are waiting for review
- accepted staged items are promoted conservatively:
  - `attribute_candidate` items write canonical local-only attributes
  - `preference` items write local preference signals with `system_inference`
    provenance
  - `correction` items write local correction-linked preference signals rather
    than silently mutating canonical identity truth
- dismissing or accepting a staged signal marks it processed without deleting
  history from `extracted_session_signals`

### 28. Temporal Intelligence Layer
- `engine/temporal_analyzer.py` detects three event types using only
  `attribute_history` and `attributes` — no new data infrastructure required
- **Drift**: attributes changed 2+ times within the last 365 days are staged
  as `drift` events; signals that a stored belief may be actively evolving
- **Shift cluster**: 3+ attributes in one domain changed within any 90-day
  window are staged as `shift_cluster` events; signals a possible life transition
- **Confidence decay**: active/confirmed attributes with confidence ≥ 0.70 not
  confirmed or updated in 540+ days are staged as `confidence_decay` events;
  prompts re-confirmation before stale high-confidence beliefs influence queries
- Decay events auto-resolve when the attribute is subsequently confirmed,
  keeping the `temporal_events` table accurate without user action
- Events are deduplicated across refresh passes so repeated runs stay idempotent
- Teach question planning now includes a third prioritization pass that stages
  confidence-decay re-confirmation questions (priority 12.0) after cross-domain
  synthesis and contradiction questions
- Coverage evaluator now accepts an optional `shift_cluster_note` that is
  appended to coverage notes when the queried domain has an active shift cluster,
  warning the user that retrieved context may be outdated
- `GET /identity/evolution` returns the full temporal event timeline (active and
  resolved) ordered by detection time for a future "How I've changed" UI view

### 29. Natural Voice Learning Layer
- `engine/voice_feature_extractor.py` extracts seven pure-statistical voice
  features from user text using only local regex — no model call:
  - `avg_sentence_length`
  - `question_frequency`
  - `first_person_density`
  - `contraction_rate`
  - `em_dash_rate`
  - `ellipsis_rate`
  - `word_count`
- extraction returns `None` for texts shorter than 50 words
- the session learner now calls the extractor after each qualifying exchange
  and persists the result in `voice_feature_observations` when the query
  meets the 50-word threshold; this fires independently of whether the LLM
  signal-extraction path is active
- a rolling aggregate baseline is maintained in `voice_baseline_profile`
  (upserted on every observation write) as a single `singleton` row
- `build_voice_profile()` now accepts an optional `conn` and, when ≥ 5
  observations exist, loads the baseline and appends learned structural
  guidance as local-only `VoiceGuidanceItem` preference lines:
  - average sentence length
  - question register (frequent / rare)
  - contraction register (informal / formal)
  - em-dash usage
  - ellipsis usage
- baseline guidance is local-only and is never sent to external providers
- extraction failures are non-blocking and silent

### 30. Deep Reflection Mode
- `engine/reflection_session_engine.py` manages multi-turn Socratic reflection sessions
- `build_reflection_session_seed()` finds the best starting point using Phase 4+5 data:
  - prefers domains with active contradiction flags
  - falls back to pending synthesis domains, then drift domains, then most-populated domain
- session state is stored in `app.state.reflection_sessions[session_id]` (in-memory) for
  the duration of the server process; no active session state is persisted to the DB
- all inference flows through `PrivacyBroker` with `task_type="reflection"`:
  - only local-only context is assembled (seed domain attributes, contradictions, syntheses)
  - contains_local_only_context is set appropriately per call
- `start_reflection_session()` creates session state and generates the first question via
  LLM; falls back to deterministic seed question when no local model is available
- `process_reflection_turn()` processes each user response:
  - appends user message to history
  - calls LLM for next question + suggested attribute updates + themes noticed
  - falls back to a deterministic turn sequence when LLM is unavailable
  - caps suggested update confidence at 0.75
  - limits suggestions to 2 items per turn
  - deduplicates themes across turns
  - stages any suggested updates as `attribute_candidate` signals in
    `extracted_session_signals` for review through the existing Teach signal-review flow
  - never auto-promotes suggestions into canonical attributes
- two new backend endpoints exposed on `server/routes/teach.py`:
  - `POST /teach/reflection/start` — creates session, returns session_id + first_question
  - `POST /teach/reflection/turn` — processes one user turn, returns next_question,
    suggested_updates, themes_noticed, staged_signal_ids, turn_count
- frontend TeachTab adds "Deep Reflect" mode:
  - button shown after onboarding is complete
  - switches to a conversational view with Q&A history
  - displays suggested attribute updates and themes as they accumulate
  - exit returns the user to the normal Teach view
  - staged suggestions can be reviewed through the existing conversation-signal review flow

### 27. Cross-Domain Synthesis Staging
- the first backend slice of Phase 4 from `docs/MAXIMIZE_INTELLIGENCE.md` is
  now implemented with deterministic local-only staging
- active high-confidence attributes can now be scanned for:
  - repeated semantic themes spanning 3+ domains
  - polarity tensions across high-confidence attributes
- cross-domain theme staging is implemented in `engine/synthesis_engine.py`
  and currently produces deterministic synthesis summaries rather than an
  LLM-written narrative
- contradiction detection is implemented in
  `engine/contradiction_detector.py` using a static polarity lexicon
- staged outputs persist in:
  - `cross_domain_synthesis`
  - `contradiction_flags`
- Teach now surfaces these reviewable items through:
  - `GET /teach/synthesis`
  - `Teach` bootstrap `synthesis_review` cards
- Teach question planning now prioritizes staged synthesis/contradiction review
  prompts ahead of generic catalog questions when pending items exist
- review remains user-mediated; no synthesis or contradiction result writes
  directly into canonical attributes
- synthesis and contradiction items can now be actioned through:
  - `POST /teach/synthesis/{id}/accept` — marks accepted; attempts optional
    local LLM narrative generation via PrivacyBroker and persists the result
    in `synthesis_text` when a local model is available; silent on failure
  - `POST /teach/synthesis/{id}/dismiss` — marks dismissed
  - `POST /teach/contradictions/{id}/resolve` — marks resolved (user has
    addressed the tension)
  - `POST /teach/contradictions/{id}/dismiss` — marks dismissed (user says
    it is not a real tension)

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
- generalized evidence summaries must remain privacy-safe and must not copy raw
  artifact content, raw supporting text, or raw feedback text

---

# What the System Can Do Now

## Intelligence Roadmap Status

- Phase 1 (`Semantic Retrieval Bridge`) is implemented on the backend:
  deterministic domain-aware concept expansion is live, retrieval includes a
  separate expanded-query score, and an optional bounded local-only similarity
  tier is available through temporary FTS5 matching plus Ollama embeddings when
  a local embedding model is already available
- Phase 2 (`Passive Session Learning`) is implemented on the backend:
  qualifying query exchanges can stage review-only conversation signals, and
  Teach now exposes list/accept/dismiss APIs for those staged items
- Phase 3 (`Feedback Recalibration Loop`) is implemented on the backend:
  feedback now drives both bounded domain-level retrieval calibration and
  conservative attribute-level confidence downgrades for repeatedly low-rated
  inferred attributes, with append-only audit history
- Phase 4 (`Cross-Domain Synthesis`) is fully implemented on the backend:
  deterministic theme detection, contradiction staging, Teach queue
  integration, synthesis review API, accept-dismiss workflows for both
  syntheses and contradiction flags, and optional local LLM narrative
  generation on synthesis acceptance are all live
- Phase 5 (`Temporal Intelligence`) is fully implemented on the backend:
  drift detection, shift-cluster detection, and confidence-decay detection
  are all live; decay events auto-resolve when the user confirms the
  attribute; Teach now surfaces confidence-decay re-confirmation questions;
  coverage notes warn when a queried domain has an active shift cluster;
  `GET /identity/evolution` exposes the full temporal event timeline
- Phase 6 (`Natural Voice Learning`) is fully implemented on the backend:
  pure-statistical voice feature extraction is live; observations are
  accumulated per-session in `voice_feature_observations`; a rolling
  aggregate baseline is maintained in `voice_baseline_profile`; the session
  learner now extracts voice features from queries ≥ 50 words without any
  model call; `build_voice_profile()` now loads the baseline when ≥ 5
  observations exist and appends learned structural guidance (sentence
  length, question frequency, contraction register, em-dash/ellipsis usage)
  as local-only preference guidance lines
- Phase 7 (`Deep Reflection Mode`) is fully implemented: multi-turn Socratic reflection
  sessions are live; seed selection uses contradiction flags, pending syntheses, and drift
  domains from Phases 4 and 5; all inference flows through PrivacyBroker; suggested updates
  stage via `extracted_session_signals` for user review; frontend TeachTab includes a
  conversational "Deep Reflect" mode
- the remaining Phase 2 gap is frontend depth rather than backend plumbing:
  the Teach bootstrap card and review endpoints exist, but a richer dedicated
  conversation-signal review workflow has not been built yet

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
- list privacy-safe generalized evidence summaries for attributes, artifacts,
  sessions, query feedback, and voice feedback through one read model
- run a deterministic query usefulness eval corpus without calling an LLM
- distinguish explicit “write in my voice” requests from generic preference-sensitive drafting
- compile bounded voice guidance from structured traits, learned preferences,
  and local writing exemplars
- include local writing exemplar snippets only for local voice-generation runs
- collect local-only voice fidelity feedback and convert repeated misses into
  lower-level voice preference signals without mutating canonical attributes
- lazy-load authenticated app tabs so slower machines do not pay the initial
  cost of every screen up front
- bridge abstract self-queries to stored identity labels using deterministic,
  domain-aware concept expansion instead of exact lexical overlap alone
- optionally add bounded local-only similarity support with temporary FTS5 and
  cached Ollama embeddings without letting that bonus override deterministic
  grounding
- passively stage review-only identity and preference hints from qualifying
  query sessions without auto-promoting them into canonical truth
- review, accept, or dismiss staged conversation signals through Teach APIs
  while preserving audit-friendly local history
- detect cross-domain identity themes and contradiction candidates locally,
  then surface them in Teach before generic onboarding prompts
- accept or dismiss staged cross-domain synthesis items, with optional local
  LLM narrative generation for accepted themes when a local model is available
- resolve or dismiss staged contradiction flags after the user has addressed
  or ruled out the identified tension
- detect identity drift when a single attribute changes repeatedly within the
  past year and stage it as a reviewable temporal event
- detect life-transition shift clusters when three or more attributes in one
  domain change within a 90-day window and flag the domain as in flux
- detect confidence decay for high-confidence attributes that have not been
  confirmed or updated in over 540 days and surface re-confirmation questions
  in Teach so stale beliefs can be validated or retired
- auto-resolve confidence-decay events when the user subsequently confirms
  the attribute, keeping the temporal event history accurate and append-only
- include a staleness warning in query coverage notes when the queried domain
  has an active shift-cluster event so the user knows retrieved context may
  be outdated
- expose the full temporal evolution timeline through `GET /identity/evolution`
  for a potential "How I've changed" UI view
- passively accumulate structural voice features from queries ≥ 50 words
  using only local regex, with no model call required
- maintain a rolling voice baseline profile from accumulated observations and
  apply it as learned structural guidance during voice-generation drafting
  when enough observations exist

---

# Known Implementation Gaps

- Cross-platform machine security inspection is incomplete. Automated posture checks are implemented for macOS; other platforms fall back to manual review guidance.
- Artifact parsing is intentionally narrow. The system supports local `.txt`, `.md`, `.pdf`, and `.docx` inputs, but it does not yet provide OCR, image understanding, or richer connector/import pipelines.
- Distribution is still source-first. The project runs well for developers from this repo, but it does not yet ship as a packaged desktop app, installer, or one-command production deployment.
