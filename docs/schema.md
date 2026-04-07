# Identity Engine — Database Schema

## Overview

All data is stored in a single SQLCipher-encrypted SQLite file at
`~/.identity-engine/identity.db`. The encryption key is held exclusively in
the system keychain; it is never written to disk in plaintext.

---

## Tables

### `domains`

Organising containers that group attributes by theme. Each domain represents a
broad area of identity (e.g. `values`, `goals`, `personality`).

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID v4 |
| `name` | TEXT UNIQUE | Human-readable name (e.g. `"values"`) |
| `description` | TEXT | Optional prose description |
| `created_at` | TIMESTAMP | Set on insert, never updated |
| `updated_at` | TIMESTAMP | Updated whenever the row changes |

**Default domains:** `personality`, `values`, `goals`, `patterns`, `voice`,
`relationships`, `fears`, `beliefs`.

---

### `attributes`

The core table. Each row is one atomic identity fact — a single thing the
system believes to be true about the user.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID v4 |
| `domain_id` | TEXT FK → domains | Required; deletion blocked if attributes exist |
| `label` | TEXT | Short identifier within the domain (e.g. `"risk_tolerance"`) |
| `value` | TEXT | The actual claim (e.g. `"moderate-high"`) |
| `elaboration` | TEXT | Optional prose expansion of the value |
| `mutability` | TEXT | `stable` or `evolving` — see below |
| `source` | TEXT | `explicit`, `inferred`, or `reflection` — see below |
| `confidence` | REAL | 0.0 – 1.0; enforced by CHECK constraint |
| `routing` | TEXT | `local_only` or `external_ok` — see below |
| `status` | TEXT | `active`, `superseded`, or `retracted` |
| `created_at` | TIMESTAMP | Set on insert |
| `updated_at` | TIMESTAMP | Updated on each change |
| `last_confirmed` | TIMESTAMP | Last time the user explicitly confirmed this is still true |

**Unique index:** only one `active` attribute per `(domain_id, label)` pair.
Older values must be moved to `superseded` before a new active row is inserted.

#### Field details

**`mutability`**
- `stable` — unlikely to change (e.g. core values, personality traits).
- `evolving` — expected to shift over time (e.g. current goals, skill levels).

**`source`**
- `explicit` — the user stated this directly.
- `inferred` — derived from patterns or evidence; see `inference_evidence`.
- `reflection` — surfaced during a guided or freeform reflection session.

**`routing`**
Controls whether this attribute may ever leave the local machine.
- `local_only` — **default**. This attribute is never sent to an external API,
  including Claude. Any query requiring this attribute must be answered by a
  local model (Ollama) or by the user directly.
- `external_ok` — the user has explicitly consented to this attribute being
  included in external API calls (e.g. general writing assistance via Claude).

The routing flag is set by the user. The application layer must enforce it; the
database records the intent.

**`confidence`**
A probability-style score between 0.0 and 1.0 representing how certain the
system is that this attribute accurately describes the user. `1.0` means the
user stated it directly and recently confirmed it. Lower values indicate
inference or staleness.

---

### `attribute_history`

Append-only audit log. A row is inserted here whenever an attribute's value or
confidence changes. Rows are never updated or deleted.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID v4 |
| `attribute_id` | TEXT FK → attributes | Deletion blocked |
| `previous_value` | TEXT | The value before the change |
| `previous_confidence` | REAL | Confidence before the change |
| `reason` | TEXT | Optional human-readable explanation |
| `changed_at` | TIMESTAMP | When the change occurred |
| `changed_by` | TEXT | `user`, `reflection`, or `inferred` |

---

### `inference_evidence`

Records the evidence trail that led to an `inferred` attribute, so the user can
audit why the system believes something about them.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID v4 |
| `attribute_id` | TEXT FK → attributes | The inferred attribute this supports |
| `source_type` | TEXT | e.g. `reflection_session`, `capture`, `vault_analysis` |
| `source_ref` | TEXT | Reference id or path to the originating source |
| `supporting_text` | TEXT | The snippet or passage that supports the inference |
| `weight` | REAL | 0.0 – 1.0; how strongly this evidence supports the attribute |
| `created_at` | TIMESTAMP | When this evidence was recorded |

---

### `reflection_sessions`

Metadata about reflection sessions. Transcripts are **not** stored here —
only aggregate statistics and routing decisions.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID v4 |
| `session_type` | TEXT | `guided`, `freeform`, or `vault_analysis` |
| `summary` | TEXT | Optional prose summary of the session |
| `attributes_created` | INTEGER | How many new attributes were created |
| `attributes_updated` | INTEGER | How many existing attributes were updated |
| `external_calls_made` | INTEGER | Number of calls made to external APIs |
| `routing_log` | TEXT | JSON array of routing decisions made during the session |
| `started_at` | TIMESTAMP | Session start time |
| `ended_at` | TIMESTAMP | Session end time (null if still in progress) |

---

## Default values

| Field | Default | Reason |
|---|---|---|
| `routing` | `local_only` | Privacy-first: opt-in to external sharing, never opt-out |
| `status` | `active` | New attributes are active by definition |
| `confidence` | *(none — required)* | Caller must make an explicit assessment |
| `attributes_created/updated` | `0` | Counters start at zero |

---

## Worked example

A complete attribute entry representing an inferred belief about the user's
preferred work style:

```sql
-- Domain
INSERT INTO domains (id, name, description)
VALUES ('d1a2b3c4-...', 'patterns', 'Recurring behavioural patterns and habits');

-- Attribute
INSERT INTO attributes (
    id, domain_id, label, value, elaboration,
    mutability, source, confidence, routing, status
) VALUES (
    'a9f8e7d6-...',
    'd1a2b3c4-...',
    'work_style',
    'deep-focus-blocks',
    'Prefers 2–3 hour uninterrupted blocks over frequent context switching. '
    'Performs best on creative and analytical tasks in the morning.',
    'evolving',
    'inferred',
    0.75,
    'local_only',   -- never send to external APIs
    'active'
);

-- Evidence that informed the inference
INSERT INTO inference_evidence (
    id, attribute_id, source_type, supporting_text, weight
) VALUES (
    'e1d2c3b4-...',
    'a9f8e7d6-...',
    'reflection_session',
    'User noted frustration with back-to-back meetings and said they do '
    'their best thinking "in long stretches without interruption".',
    0.8
);
```

**Reading the routing flag:** `local_only` means this attribute will never be
included in a request to Claude or any other external service. A local Ollama
model may use it. If the user later decides this is safe to share, they update
`routing` to `external_ok` — and that change is logged in `attribute_history`.
