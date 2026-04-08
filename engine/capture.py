"""Quick-capture ingestion for writing identity attributes without a session.

This module accepts a short free-text capture, asks the configured LLM to
extract structured identity attributes, optionally confirms them with the user,
handles label conflicts, and writes confirmed attributes directly to the
identity store.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid

from config.llm_router import generate_response
from config.settings import EVOLVING, EXPLICIT, LOCAL_ONLY

logger = logging.getLogger(__name__)

DEFAULT_CAPTURE_CONFIDENCE = 0.5

VALID_DOMAINS = {
    "personality",
    "values",
    "goals",
    "patterns",
    "voice",
    "relationships",
    "fears",
    "beliefs",
}

EXTRACTION_SYSTEM_PROMPT = """You are a structured data extractor for a personal identity
store. The user has provided a quick capture — a short thought,
observation, or update about themselves. Extract one or more
identity attributes from it.

For each attribute output a JSON object with these exact fields:
- domain: one of: personality, values, goals, patterns, voice,
  relationships, fears, beliefs
- label: short snake_case identifier (e.g. 'response_to_change')
- value: clear specific description, 1-3 sentences, first person
  where natural
- elaboration: additional nuance or null
- mutability: 'stable' or 'evolving'
- confidence: float 0.0 to 1.0. Be conservative — quick captures
  are lower confidence than guided reflection. Max 0.75 unless
  the input is very definitive.

If a domain_hint is provided, prefer that domain unless the
content clearly belongs elsewhere.

Return a JSON array only. No preamble, no markdown fences."""


def _validate_domain_hint(domain_hint: str | None) -> None:
    if domain_hint is None:
        return
    if domain_hint not in VALID_DOMAINS:
        allowed = ", ".join(sorted(VALID_DOMAINS))
        raise ValueError(f"Invalid domain hint '{domain_hint}'. Expected one of: {allowed}")


def _build_messages(text: str, domain_hint: str | None) -> list[dict[str, str]]:
    user_message = text
    if domain_hint is not None:
        user_message = f"{text}\n\nDomain hint: {domain_hint}"
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def _parse_attributes(raw: str) -> list[dict]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Capture extraction did not return valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError("Capture extraction must return a JSON array.")
    return [_normalize_attribute(attr) for attr in parsed]


def preview_capture(
    text: str,
    domain_hint: str | None,
    provider_config,
) -> list[dict]:
    """Extract quick-capture attributes without writing them to the database."""
    _validate_domain_hint(domain_hint)
    raw = generate_response(_build_messages(text, domain_hint), provider_config)
    assert isinstance(raw, str)
    return _parse_attributes(raw)


def _normalize_attribute(attr: object) -> dict:
    if not isinstance(attr, dict):
        raise ValueError("Each extracted attribute must be a JSON object.")

    required = {"domain", "label", "value"}
    missing = sorted(required.difference(attr.keys()))
    if missing:
        raise ValueError(f"Extracted attribute is missing required fields: {', '.join(missing)}")

    domain = str(attr["domain"]).strip()
    if domain not in VALID_DOMAINS:
        raise ValueError(f"Extracted attribute has invalid domain '{domain}'.")

    raw_mutability = str(attr.get("mutability", EVOLVING)).strip() or EVOLVING
    mutability = raw_mutability
    if mutability not in {"stable", "evolving"}:
        raise ValueError(f"Extracted attribute has invalid mutability '{mutability}'.")

    raw_confidence = attr.get("confidence", DEFAULT_CAPTURE_CONFIDENCE)
    confidence = min(float(raw_confidence), 0.75)

    return {
        "domain": domain,
        "label": str(attr["label"]).strip(),
        "value": str(attr["value"]).strip(),
        "elaboration": attr.get("elaboration"),
        "mutability": mutability,
        "confidence": confidence,
    }


def _preview(attributes: list[dict]) -> None:
    print("\n--- Quick Capture Preview ---")
    for index, attr in enumerate(attributes, 1):
        print(
            f"[{index}] [{attr['domain']}] {attr['label']} "
            f"({attr['mutability']}, confidence: {attr['confidence']:.2f})"
        )
        print(f'    Value: "{attr["value"]}"')
        if attr.get("elaboration"):
            print(f'    Elaboration: "{attr["elaboration"]}"')
    print()
    print("Press Enter or type 'y' to save all extracted attributes.")
    print("Type 's' or 'n' to cancel this capture.")


def _confirm_all(attributes: list[dict]) -> bool:
    _preview(attributes)
    while True:
        try:
            choice = input("> ").strip().lower()
        except EOFError:
            return False
        if choice in {"", "y"}:
            return True
        if choice in {"s", "n"}:
            return False
        print("  Enter to save all, or type 's' to cancel.")


def _get_domain_id(conn, domain_name: str) -> str:
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (domain_name,)).fetchone()
    if row is None:
        raise RuntimeError(f"Domain '{domain_name}' not found. Run 'make init' to seed domains.")
    return str(row[0])


def _find_existing_active(conn, domain_id: str, label: str):
    return conn.execute(
        "SELECT id, value, confidence FROM attributes "
        "WHERE domain_id = ? AND label = ? AND status = 'active'",
        (domain_id, label),
    ).fetchone()


def _next_available_label(conn, domain_id: str, base_label: str) -> str:
    suffix = 2
    while True:
        candidate = f"{base_label}_{suffix}"
        if _find_existing_active(conn, domain_id, candidate) is None:
            return candidate
        suffix += 1


def _prompt_conflict(domain: str, label: str, existing_value: str, new_value: str) -> str:
    print(f"\nConflict: '{label}' already exists in {domain}.\n")
    print(f'Existing: "{existing_value}"')
    print(f'New:      "{new_value}"\n')
    print("(u)pdate - mark existing as superseded, write new")
    print("(s)kip    - keep existing, discard new")
    print("(k)eep both - write new with a modified label (appends _2)")

    while True:
        try:
            choice = input("> ").strip().lower()
        except EOFError:
            return "skip"
        if choice in {"u", "update"}:
            return "update"
        if choice in {"s", "skip"}:
            return "skip"
        if choice in {"k", "keep both", "keep"}:
            return "keep_both"
        print("  Enter 'u', 's', or 'k'.")


def _insert_attribute(conn, attr: dict, domain_id: str, now: str) -> dict:
    saved = {
        **attr,
        "id": str(uuid.uuid4()),
        "domain_id": domain_id,
        "source": EXPLICIT,
        "routing": LOCAL_ONLY,
        "status": "active",
        "confidence": min(float(attr["confidence"]), 0.75),
    }
    conn.execute(
        "INSERT INTO attributes "
        "(id, domain_id, label, value, elaboration, mutability, source, confidence, "
        "routing, status, created_at, updated_at, last_confirmed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            saved["id"],
            domain_id,
            saved["label"],
            saved["value"],
            saved.get("elaboration"),
            saved.get("mutability", EVOLVING),
            saved["source"],
            saved["confidence"],
            saved["routing"],
            saved["status"],
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return saved


def _supersede_existing(conn, old_row, now: str) -> None:
    old_id, old_value, old_confidence = old_row
    conn.execute(
        "UPDATE attributes SET status = 'superseded', updated_at = ? WHERE id = ?",
        (now, old_id),
    )
    conn.execute(
        "INSERT INTO attribute_history "
        "(id, attribute_id, previous_value, previous_confidence, reason, changed_at, changed_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 'user')",
        (
            str(uuid.uuid4()),
            old_id,
            old_value,
            old_confidence,
            "quick capture update",
            now,
        ),
    )


def _save_attribute(conn, attr: dict, old_row, announce: bool) -> dict:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    domain_id = _get_domain_id(conn, attr["domain"])
    if old_row is not None:
        _supersede_existing(conn, old_row, now)
    saved = _insert_attribute(conn, attr, domain_id, now)
    if announce:
        print(f"Saved: [{saved['domain']}] {saved['label']}")
    return saved


def _resolve_conflict(conn, attr: dict, confirm: bool) -> dict | None:
    domain_id = _get_domain_id(conn, attr["domain"])
    existing = _find_existing_active(conn, domain_id, attr["label"])
    if existing is None:
        return _save_attribute(conn, attr, None, announce=confirm)

    if not confirm:
        logger.warning(
            "Skipping quick capture conflict for %s.%s in non-interactive mode.",
            attr["domain"],
            attr["label"],
        )
        return None

    choice = _prompt_conflict(attr["domain"], attr["label"], str(existing[1]), attr["value"])
    if choice == "skip":
        return None
    if choice == "keep_both":
        updated_attr = dict(attr)
        updated_attr["label"] = _next_available_label(conn, domain_id, attr["label"])
        return _save_attribute(conn, updated_attr, None, announce=True)
    return _save_attribute(conn, attr, existing, announce=True)


def capture(
    text: str,
    domain_hint: str | None,
    conn,
    provider_config,
    confirm: bool = True,
) -> list[dict]:
    """Extract and persist quick-capture attributes.

    Args:
        text: Raw free-text identity capture.
        domain_hint: Optional domain name to bias extraction toward.
        conn: Database connection supplied by db.connection.get_connection().
        provider_config: LLM routing config from config.llm_router.resolve_router().
        confirm: When True, preview and confirm before writing.

    Returns:
        The list of attribute dicts that were actually written.
    """
    attributes = preview_capture(text, domain_hint, provider_config)

    if not attributes:
        return []

    if confirm and not _confirm_all(attributes):
        return []

    saved: list[dict] = []
    for attr in attributes:
        written = _resolve_conflict(conn, attr, confirm)
        if written is not None:
            saved.append(written)
    return saved
