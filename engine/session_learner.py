"""Passive session-learning helpers for staging reviewable conversation signals."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from config.llm_router import ConfigurationError
from engine.privacy_broker import PrivacyBroker
from engine.session import Session
from engine.setup_state import resolve_local_provider_config

logger = logging.getLogger(__name__)

_VALID_DOMAINS = {
    "beliefs",
    "fears",
    "goals",
    "patterns",
    "personality",
    "relationships",
    "values",
    "voice",
}
_PREFERENCE_SIGNALS = {"like", "dislike", "accept", "reject", "prefer", "avoid"}
_MUTABILITY_VALUES = {"stable", "evolving"}
_WORD_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)
_FIRST_PERSON_RE = re.compile(r"\b(i|i'm|im|me|my|mine|myself)\b", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_MAX_QUERY_WORDS = 20
_MAX_QUERY_EXCERPT_CHARS = 280
_MAX_SIGNAL_ITEMS = 3
_EXTRACTION_TIMEOUT_SECONDS = 20
_CORRECTION_MARKERS = (
    "actually",
    "not quite",
    "that's not right",
    "that is not right",
    "i meant",
    "to clarify",
    "more accurately",
    "rather",
    "instead",
)

_SIGNAL_EXTRACTION_PROMPT = """You stage conservative, reviewable passive-learning candidates for a privacy-first identity engine.
Return JSON only with this exact object shape:
{
  "attribute_candidates": [
    {
      "domain": "personality|values|goals|patterns|voice|relationships|fears|beliefs",
      "label": "snake_case",
      "value": "first-person grounded statement",
      "elaboration": "optional nuance or null",
      "mutability": "stable|evolving",
      "confidence": 0.0
    }
  ],
  "preference_signals": [
    {
      "category": "snake_case",
      "subject": "snake_case",
      "signal": "like|dislike|accept|reject|prefer|avoid",
      "strength": 1,
      "summary": "short grounded reason"
    }
  ]
}

Rules:
- Use only information explicitly stated by the user.
- Be conservative. If the message is not clearly self-descriptive, return empty arrays.
- Do not infer hidden motives or biography.
- Keep at most 3 attribute candidates and 3 preference signals.
- Attribute confidence must stay between 0.0 and 0.75.
- Preference summaries should stay short and factual.
- Return JSON only, with no markdown fences or commentary.
"""

_CORRECTION_EXTRACTION_PROMPT = """You detect whether a user message is correcting or narrowing prior identity framing.
Return JSON only as an array. Each item must be an object with:
- summary: short description of the correction
- correction_text: concise paraphrase of what the user corrected
- confidence: float between 0.0 and 0.8
- attribute_ids: array of ids chosen only from the provided candidate attribute list

Rules:
- Return [] when the user is not clearly correcting prior framing.
- Only use attribute ids that are explicitly provided in the candidate list.
- Be conservative and prefer precision over recall.
- Return JSON only, with no markdown fences or commentary.
"""


def _slug(value: str) -> str:
    normalized = _NON_WORD_RE.sub("_", value.lower()).strip("_")
    return normalized or "candidate"


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _contains_first_person(text: str) -> bool:
    return bool(_FIRST_PERSON_RE.search(text))


def _matched_correction_markers(text: str) -> list[str]:
    lowered = text.lower()
    return [marker for marker in _CORRECTION_MARKERS if marker in lowered]


def _query_excerpt(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= _MAX_QUERY_EXCERPT_CHARS:
        return compact
    return compact[: _MAX_QUERY_EXCERPT_CHARS - 3].rstrip() + "..."


def _strip_json_fences(raw: str) -> str:
    content = raw.strip()
    if content.startswith("```"):
        lines = [line for line in content.splitlines() if not line.strip().startswith("```")]
        return "\n".join(lines).strip()
    return content


def _resolve_learning_provider(provider_config: Any) -> Any | None:
    try:
        return resolve_local_provider_config(provider_config)
    except ConfigurationError:
        logger.debug("Skipping passive session learning because no local model is available.")
        return None


def _build_signal_messages(
    *,
    user_query: str,
    source_profile: str,
    domain_hints: list[str],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _SIGNAL_EXTRACTION_PROMPT},
        {
            "role": "user",
            "content": (
                f"Source profile: {source_profile}\n"
                f"Domain hints: {', '.join(domain_hints) if domain_hints else 'none'}\n"
                "User message:\n"
                f"{user_query}"
            ),
        },
    ]


def _format_history(history: list[dict[str, Any]]) -> str:
    if not history:
        return "No earlier session history."
    entries: list[str] = []
    for item in history[-4:]:
        role = str(item.get("role", "unknown"))
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        entries.append(f"{role}: {_query_excerpt(content)}")
    return "\n".join(entries) if entries else "No earlier session history."


def _build_correction_messages(
    *,
    user_query: str,
    prior_history: list[dict[str, Any]],
    retrieved_attributes: list[dict[str, Any]],
) -> list[dict[str, str]]:
    attribute_lines = [
        (
            f"- id={attr.get('id')} | domain={attr.get('domain', '')} | "
            f"label={attr.get('label', '')} | value={_query_excerpt(str(attr.get('value', '')))}"
        )
        for attr in retrieved_attributes
        if attr.get("id")
    ]
    return [
        {"role": "system", "content": _CORRECTION_EXTRACTION_PROMPT},
        {
            "role": "user",
            "content": (
                "Recent session history:\n"
                f"{_format_history(prior_history)}\n\n"
                "Candidate retrieved attributes:\n"
                f"{chr(10).join(attribute_lines) if attribute_lines else 'None'}\n\n"
                "Latest user message:\n"
                f"{user_query}"
            ),
        },
    ]


def _load_json_object(raw: str) -> dict[str, Any]:
    parsed = json.loads(_strip_json_fences(raw))
    if not isinstance(parsed, dict):
        raise ValueError("Session learner signal extraction must return a JSON object.")
    return parsed


def _load_json_list(raw: str) -> list[dict[str, Any]]:
    parsed = json.loads(_strip_json_fences(raw))
    if not isinstance(parsed, list):
        raise ValueError("Session learner correction extraction must return a JSON array.")
    return [item for item in parsed if isinstance(item, dict)]


def _normalize_attribute_candidate(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    domain = str(item.get("domain", "")).strip().lower()
    label = _slug(str(item.get("label", "")).strip())
    value = str(item.get("value", "")).strip()
    if domain not in _VALID_DOMAINS or not label or not value:
        return None
    mutability = str(item.get("mutability", "evolving")).strip().lower()
    if mutability not in _MUTABILITY_VALUES:
        mutability = "evolving"
    try:
        confidence = float(item.get("confidence", 0.55) or 0.55)
    except (TypeError, ValueError):
        confidence = 0.55
    confidence = max(0.0, min(confidence, 0.75))
    elaboration = item.get("elaboration")
    return {
        "domain": domain,
        "label": label,
        "value": value,
        "elaboration": str(elaboration).strip() if elaboration not in {None, ""} else None,
        "mutability": mutability,
        "confidence": confidence,
    }


def _normalize_preference_signal(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    category = _slug(str(item.get("category", "")).strip())
    subject = _slug(str(item.get("subject", "")).strip())
    signal = str(item.get("signal", "")).strip().lower()
    if not category or not subject or signal not in _PREFERENCE_SIGNALS:
        return None
    try:
        strength = int(item.get("strength", 3) or 3)
    except (TypeError, ValueError):
        strength = 3
    strength = max(1, min(strength, 5))
    summary = str(item.get("summary", "")).strip()
    return {
        "category": category,
        "subject": subject,
        "signal": signal,
        "strength": strength,
        "summary": summary or f"Conversation suggests {signal} {subject.replace('_', ' ')}.",
    }


def _normalize_correction(
    item: object,
    *,
    allowed_attribute_ids: set[str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    summary = str(item.get("summary", "")).strip()
    correction_text = str(item.get("correction_text", "")).strip()
    if not summary and not correction_text:
        return None
    try:
        confidence = float(item.get("confidence", 0.65) or 0.65)
    except (TypeError, ValueError):
        confidence = 0.65
    attribute_ids = [
        str(attr_id)
        for attr_id in item.get("attribute_ids", [])
        if str(attr_id) in allowed_attribute_ids
    ]
    return {
        "summary": summary or correction_text,
        "correction_text": correction_text or summary,
        "confidence": max(0.0, min(confidence, 0.8)),
        "attribute_ids": attribute_ids,
    }


def _extract_signal_candidates(
    *,
    provider_config: Any,
    user_query: str,
    source_profile: str,
    domain_hints: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result = PrivacyBroker(provider_config).extract_structured_attributes(
        _build_signal_messages(
            user_query=user_query,
            source_profile=source_profile,
            domain_hints=domain_hints,
        ),
        task_type="session_learning_signal_extraction",
        timeout_seconds=_EXTRACTION_TIMEOUT_SECONDS,
    )
    payload = _load_json_object(result.content)
    attributes = [
        normalized
        for normalized in (
            _normalize_attribute_candidate(item)
            for item in payload.get("attribute_candidates", [])
        )
        if normalized is not None
    ][:_MAX_SIGNAL_ITEMS]
    preferences = [
        normalized
        for normalized in (
            _normalize_preference_signal(item)
            for item in payload.get("preference_signals", [])
        )
        if normalized is not None
    ][:_MAX_SIGNAL_ITEMS]
    return attributes, preferences


def _extract_corrections(
    *,
    provider_config: Any,
    user_query: str,
    prior_history: list[dict[str, Any]],
    retrieved_attributes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed_attribute_ids = {str(attr.get("id")) for attr in retrieved_attributes if attr.get("id")}
    result = PrivacyBroker(provider_config).extract_structured_attributes(
        _build_correction_messages(
            user_query=user_query,
            prior_history=prior_history,
            retrieved_attributes=retrieved_attributes,
        ),
        task_type="session_learning_correction_detection",
        timeout_seconds=_EXTRACTION_TIMEOUT_SECONDS,
    )
    payload = _load_json_list(result.content)
    return [
        normalized
        for normalized in (
            _normalize_correction(item, allowed_attribute_ids=allowed_attribute_ids)
            for item in payload
        )
        if normalized is not None
    ][:_MAX_SIGNAL_ITEMS]


def _insert_staged_signal(
    conn,
    *,
    session_id: str,
    exchange_index: int,
    signal_type: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO extracted_session_signals (
            id,
            session_id,
            exchange_index,
            signal_type,
            payload_json,
            processed
        )
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (
            str(uuid.uuid4()),
            session_id,
            exchange_index,
            signal_type,
            json.dumps(payload, sort_keys=True),
        ),
    )


def maybe_extract_from_exchange(
    conn,
    session: Session,
    *,
    user_query: str,
    coverage_confidence: str,
    retrieved_attributes: list[dict[str, Any]],
    provider_config: Any,
    source_profile: str,
    domain_hints: list[str] | None = None,
) -> int:
    """Stage passive learning signals from a completed exchange.

    This path is intentionally best-effort and non-blocking: when no suitable
    local model is available or extraction fails, it returns ``0``.
    """
    if coverage_confidence == "high_confidence":
        return 0
    if _word_count(user_query) < _MAX_QUERY_WORDS:
        return 0

    correction_markers = _matched_correction_markers(user_query)
    if not _contains_first_person(user_query) and not correction_markers:
        return 0

    learning_provider = _resolve_learning_provider(provider_config)
    if learning_provider is None:
        return 0

    resolved_domain_hints = sorted({hint for hint in (domain_hints or []) if hint})
    exchange_index = max(session.query_count - 1, 0)
    prior_history = session.get_history()[:-2] if len(session.get_history()) >= 2 else []
    query_excerpt = _query_excerpt(user_query)
    staged_count = 0

    try:
        attribute_candidates, preference_signals = _extract_signal_candidates(
            provider_config=learning_provider,
            user_query=user_query,
            source_profile=source_profile,
            domain_hints=resolved_domain_hints,
        )
    except Exception:
        logger.exception("Passive session signal extraction failed for session %s.", session.id)
        attribute_candidates, preference_signals = [], []

    try:
        corrections = (
            _extract_corrections(
                provider_config=learning_provider,
                user_query=user_query,
                prior_history=prior_history,
                retrieved_attributes=retrieved_attributes,
            )
            if correction_markers
            else []
        )
    except Exception:
        logger.exception("Passive session correction detection failed for session %s.", session.id)
        corrections = []

    for candidate in attribute_candidates:
        payload = {
            **candidate,
            "source_profile": source_profile,
            "domain_hints": resolved_domain_hints,
            "query_excerpt": query_excerpt,
        }
        _insert_staged_signal(
            conn,
            session_id=session.id,
            exchange_index=exchange_index,
            signal_type="attribute_candidate",
            payload=payload,
        )
        staged_count += 1

    for signal in preference_signals:
        payload = {
            **signal,
            "source_profile": source_profile,
            "domain_hints": resolved_domain_hints,
            "query_excerpt": query_excerpt,
        }
        _insert_staged_signal(
            conn,
            session_id=session.id,
            exchange_index=exchange_index,
            signal_type="preference",
            payload=payload,
        )
        staged_count += 1

    for correction in corrections:
        payload = {
            **correction,
            "source_profile": source_profile,
            "domain_hints": resolved_domain_hints,
            "matched_phrases": correction_markers,
            "query_excerpt": query_excerpt,
        }
        _insert_staged_signal(
            conn,
            session_id=session.id,
            exchange_index=exchange_index,
            signal_type="correction",
            payload=payload,
        )
        staged_count += 1

    if staged_count:
        conn.commit()
    return staged_count
