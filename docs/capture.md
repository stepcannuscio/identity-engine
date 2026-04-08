# Quick Capture — Reference

`scripts/capture.py` is the low-friction ingest path for adding identity
attributes from a short free-text note. It extracts attributes, optionally
confirms them, checks for conflicts, and writes confirmed rows directly to the
encrypted store without creating a reflection session.

---

## Running

Make target usage:

```sh
make capture TEXT="your text here"
make capture TEXT="your text here" DOMAIN=goals
make capture TEXT="I notice I avoid asking for help even when I clearly need it" DOMAIN=patterns
```

Arguments:

- `TEXT` — required free-text capture content
- `DOMAIN` — optional domain hint: `personality`, `values`, `goals`,
  `patterns`, `voice`, `relationships`, `fears`, or `beliefs`

Examples:

```sh
make capture TEXT="I've been waking up early naturally and feeling more focused in the mornings"
make capture TEXT="my main goal right now is landing a role in Seattle by end of summer" DOMAIN=goals
```

You can also run the script directly:

```sh
.venv/bin/python scripts/capture.py --text "I notice I avoid asking for help even when I clearly need it" --domain patterns
```

---

## Flow

1. Resolve the configured LLM backend and print the routing report.
2. Send the quick capture text to `generate_response()` with the structured
   extractor prompt.
3. Show a single preview of all extracted attributes.
4. If confirmed, process each attribute one at a time:
   - write immediately when there is no conflict
   - prompt for `update`, `skip`, or `keep both` when an active label already
     exists in the same domain
5. Print a completion summary with the number of attributes saved.

Quick capture writes are atomic per attribute. There is no session object and
no `reflection_sessions` row is created.

---

## Fixed write behavior

Every saved quick-capture attribute is written with:

| Field | Value |
|---|---|
| `source` | `explicit` |
| `routing` | `local_only` |
| `status` | `active` |

`confidence` is clamped to `0.75` even if the model returns a higher number.

---

## Conflict handling

Conflicts are detected by active `(domain, label)` pairs.

- `update` supersedes the old attribute, writes the new one, and appends an
  `attribute_history` row with `changed_by = 'user'` and reason
  `"quick capture update"`
- `skip` keeps the existing attribute unchanged
- `keep both` writes the new attribute with `_2`; if `_2` is already active,
  it uses `_3`, `_4`, and so on until a free label is found

In non-interactive use (`confirm=False` in the library API), conflicts default
to `skip` and emit a warning through the logger.
