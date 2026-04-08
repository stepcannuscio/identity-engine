# Identity Store Viewer — Reference

`scripts/view_db.py` pretty-prints the contents of the encrypted identity store
to the terminal, grouped by domain, with per-domain attribute counts and a
summary footer.

---

## Prerequisites

| Requirement | Check | Fix |
|---|---|---|
| Database initialised | `~/.identity-engine/identity.db` exists | `make init` |
| Encryption key present | set during `make init` | `make init` |

---

## Running

```sh
make view
# or directly:
.venv/bin/python scripts/view_db.py
```

---

## Output format

```
════════════════════════════════════════════════
  IDENTITY STORE  —  7 attributes across 3 domains
════════════════════════════════════════════════

── GOALS (2) ────────────────────────────────────
  six_month_goal   [stable, reflection, 0.90] local_only
    Ship the identity-engine application layer.
    Defined during the April guided interview.

  success_def      [evolving, reflection, 0.75] local_only
    Sustainable output with clear impact.

── PERSONALITY (5) ──────────────────────────────
  ...

── VALUES (0) ───────────────────────────────────
  (no active attributes)

────────────────────────────────────────────────
  3 domains with data  ·  5 domains empty  ·  7 total attributes
  Last updated: 2026-04-06 14:23:01
────────────────────────────────────────────────
```

### Header

The top line shows the total number of active attributes and how many domains
contain at least one.

### Domain sections

Each domain appears as a section headed by `── DOMAIN_NAME (N) ───`, where `N`
is the count of active attributes in that domain. Domains with no active
attributes show `(no active attributes)` as a placeholder — they are always
included so you can see which areas of the store are still empty.

### Attribute lines

Each active attribute is printed as:

```
  label_name   [mutability, source, confidence] routing
    Value text.
    Elaboration text (if present).
```

| Field | Description |
|---|---|
| `label` | Short identifier, e.g. `recharge_style` |
| `mutability` | `stable` or `evolving` |
| `source` | `explicit`, `inferred`, or `reflection` |
| `confidence` | 0.00–1.00 |
| `routing` | `local_only` or `external_ok` |
| Value | The main claim about the user |
| Elaboration | Optional supporting context (omitted if absent) |

Labels within a domain are left-aligned to the width of the longest label in
that domain for readability.

### Footer

```
  N domains with data  ·  M domains empty  ·  T total attributes
  Last updated: YYYY-MM-DD HH:MM:SS
```

`Last updated` shows the `updated_at` timestamp of the most recently modified
active attribute. It is omitted when the store is empty.

---

## What is shown

Only `status = 'active'` attributes are displayed. `superseded` and `retracted`
rows are excluded — they remain in the database for audit purposes but do not
appear in the viewer.

All 8 seeded domains are always shown, even if they have no active attributes,
so you can see at a glance which areas of your identity store are populated.

---

## Error handling

If the database has not been initialised (encryption key missing from the
keychain), the script prints a clear error and exits with code 1:

```
Error: Database encryption key not found in system keychain. ...
Run 'make init' to initialise the database.
```
