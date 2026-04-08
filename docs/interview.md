# Identity Interview — Reference

`scripts/seed_interview.py` is the primary way to populate the identity store
before a full UI exists. It conducts a structured terminal interview, uses a
local Ollama model to extract attributes from your answers, lets you review and
edit before anything is written, then persists confirmed attributes directly to
the encrypted database.

---

## Prerequisites

| Requirement | Check | Fix |
|---|---|---|
| Database initialised | `~/.identity-engine/identity.db` exists | `make init` |
| Ollama installed | `which ollama` returns a path | Install from https://ollama.com |

The script manages Ollama automatically — you do not need to start it or pull
the model before running `make interview`.

---

## Running

```sh
make interview
# or directly:
.venv/bin/python scripts/seed_interview.py
```

---

## Session flow

### 1. Ollama startup

The script manages Ollama automatically:

- If Ollama is already running, it proceeds silently.
- If Ollama is not running, it starts `ollama serve` in the background. Output
  from the server is appended to `~/.identity-engine/ollama.log`, not the
  terminal. The script polls for up to 10 seconds; if the server does not
  respond it exits with an error pointing to the log file.
- If `llama3.1:8b` is not yet pulled, it runs `ollama pull llama3.1:8b`
  automatically and waits for the download to complete before proceeding.
- If the script started Ollama, it terminates the server process on exit
  (clean or Ctrl+C). If Ollama was already running when the script started,
  it is left running.

Then the script verifies the database is accessible. If the database is not
found it prints a clear error and exits.

### 2. Domain selection

```
Domains available:
  1. Personality — Core personality traits, thinking styles, and behavioral defaults.
  2. Values — Deeply held values, ethical commitments, and non-negotiables.
  ...

Would you like to go through all domains, or focus on specific ones?
Enter 'all' or a comma-separated list of numbers (e.g. '1,3,5'):
```

### 3. Question loop

Questions are asked one at a time. After each answer Ollama extracts one or
more structured attributes and shows a numbered preview:

```
--- Preview ---
[1] recharge_style (stable, confidence: 0.85)
    Value: "I recharge through solitude and quiet time."
    Elaboration: "Most pronounced after large social events."

Options:
  Press Enter to confirm all
  Type numbers to skip specific ones (e.g. '2,3')
  Type 'e<N>' to edit a value (e.g. 'e1')
  Type 'r' to rephrase your answer and retry
  Type 's' to skip this question entirely
```

### 4. Confirmation and writes

- **Enter** — confirm all extracted attributes and write them to the database
- **`1,3`** — skip attributes 1 and 3, confirm the rest
- **`e2`** — edit the value field of attribute 2 in-place, then re-confirm
- **`r`** — discard the extraction, re-answer the question, re-run Ollama
- **`s`** — skip the question entirely; nothing is written

Each confirmed attribute is written **immediately** — progress is not lost if
the script crashes after a save.

If a label already exists as an active attribute in the same domain, you are
asked whether to update it (which supersedes the old value and records a
history entry) or skip.

### 5. Domain summary and continuation

After each domain:

```
Domain complete: 3 attribute(s) saved.
Continue to next domain? (y/n/q to quit)
```

`q` exits and saves the session record. `n` stops at the current domain.

### 6. Session record

At the end of every session — including interrupted ones (Ctrl+C) — a row is
written to `reflection_sessions`:

| Field | Value |
|---|---|
| `session_type` | `guided` |
| `summary` | `"Guided interview covering: personality, values"` |
| `attributes_created` | count of new attributes written |
| `attributes_updated` | count of attributes that superseded an older value |
| `external_calls_made` | always `0` — Ollama is local |
| `started_at` / `ended_at` | actual wall-clock timestamps |

---

## Domains and questions

| Domain | Questions |
|---|---|
| personality | 7 |
| values | 4 |
| goals | 4 |
| patterns | 5 |
| voice | 3 |
| relationships | 4 |
| fears | 3 |
| beliefs | 4 |

Total: **34 questions** across all domains.

---

## Data written to the database

Every confirmed attribute is stored in the `attributes` table with these
fixed values regardless of Ollama's suggestion:

| Field | Value | Reason |
|---|---|---|
| `source` | `reflection` | Set by interview sessions |
| `routing` | `local_only` | Privacy-first default; never overridden |
| `status` | `active` | New rows are always active |

The `label`, `value`, `elaboration`, `mutability`, and `confidence` fields
come from Ollama's extraction and can be edited before confirming.

When an existing active attribute is superseded, the old row's `status` is
set to `superseded` and a row is appended to `attribute_history` with
`changed_by = 'reflection'`.

---

## Privacy guarantees

- Ollama runs entirely on your machine; no answer text leaves the local network
- `external_calls_made` is always recorded as `0` in the session log
- The `routing` field is always `local_only` — it cannot be set to `external_ok`
  by the interview script
- No answer text is persisted — only the extracted structured attributes

---

## Re-running

The interview is safe to run multiple times. If you re-answer a question whose
label already exists, you are prompted to update or skip. Updates supersede the
old value and preserve the full history in `attribute_history`.

Run `make test` after a session to verify the data integrity constraints still
hold.
