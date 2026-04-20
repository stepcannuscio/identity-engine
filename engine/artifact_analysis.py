"""Local-only artifact analysis and promotion helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import re
from typing import Any, cast

import requests

from db.preference_signals import PreferenceSignalInput, record_preference_signal
from engine.artifact_ingestion import (
    get_artifact_record,
    get_artifact_tags,
    update_artifact_metadata,
)
from engine.capture import save_preview_attributes
from engine.privacy_broker import PrivacyBroker

logger = logging.getLogger(__name__)
_ATTRIBUTE_DOMAINS = {
    "beliefs",
    "fears",
    "goals",
    "patterns",
    "personality",
    "relationships",
    "values",
    "voice",
}
_MUTABILITY_VALUES = {"stable", "evolving"}
_PREFERENCE_SIGNALS = {"like", "dislike", "accept", "reject", "prefer", "avoid"}
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_WORD_RE = re.compile(r"[a-z0-9]+")
_FIRST_PERSON_RE = re.compile(r"\b(i|my|me|mine)\b", re.IGNORECASE)
_RECIPE_HINT_RE = re.compile(r"\b(recipe|recipes|dinner|meal|cook|cooking|ingredients)\b", re.IGNORECASE)
_JOURNAL_HINT_RE = re.compile(r"\b(journal|diary|reflection|reflections|entry|entries)\b", re.IGNORECASE)
_TRANSCRIPT_HINT_RE = re.compile(r"\b(transcript|meeting|interview|call|conversation)\b", re.IGNORECASE)
_READING_HINT_RE = re.compile(r"\b(book|books|reading|article|articles)\b", re.IGNORECASE)
_MAX_DESCRIPTOR_TOKENS = 12
_MAX_CANDIDATE_ATTRIBUTES = 6
_MAX_CANDIDATE_PREFERENCES = 6
_SUMMARY_SENTENCE_LIMIT = 220
_ARTIFACT_ANALYSIS_TIMEOUT_SECONDS = 30
_FALLBACK_WARNING = (
    "The local model timed out, so this upload was analyzed with a lightweight local fallback."
)

_ANALYSIS_PROMPT = """You analyze one local artifact for a privacy-first identity engine.
Return compact JSON only with these keys:
- content_kind: short snake_case label such as recipe_collection, journal, notes, transcript
- summary: 1-2 sentences about what the artifact appears to contain
- descriptor_tokens: up to 12 lowercase tokens or short phrases useful for retrieval
- candidate_attributes: array of possible canonical identity facts worth reviewing
- candidate_preferences: array of possible preference signals worth reviewing

candidate_attributes items must use:
- domain: one of beliefs, fears, goals, patterns, personality, relationships, values, voice
- label: short snake_case label
- value: grounded statement supported by the artifact
- elaboration: nuance or null
- mutability: stable or evolving
- confidence: float 0.0 to 1.0

candidate_preferences items must use:
- category: short snake_case category
- subject: short snake_case subject
- signal: one of like, dislike, accept, reject, prefer, avoid
- strength: integer 1 to 5
- summary: short explanation of the preference evidence

Only propose candidates that are directly supported by the artifact. Do not infer biography or strong preferences from weak clues.
If the artifact mostly contains recipes, logs, or examples, prefer descriptor_tokens and summary over speculative identity facts.
"""


@dataclass(frozen=True)
class ArtifactAnalysisResult:
    """One normalized artifact analysis payload."""

    artifact_id: str
    content_kind: str
    summary: str
    descriptor_tokens: list[str]
    candidate_attributes: list[dict[str, object]]
    candidate_preferences: list[dict[str, object]]
    analyzed_at: str
    analysis_method: str = "model"
    analysis_warning: str | None = None


def _slug(value: str) -> str:
    normalized = _NON_WORD_RE.sub("_", value.lower()).strip("_")
    return normalized or "candidate"


def _strip_json_fences(raw: str) -> str:
    content = raw.strip()
    if content.startswith("```"):
        lines = [line for line in content.splitlines() if not line.strip().startswith("```")]
        return "\n".join(lines).strip()
    return content


def _normalize_descriptor_tokens(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        token = str(item).strip().lower()
        if not token:
            continue
        normalized.append(token[:48])
    return sorted(dict.fromkeys(normalized))[:_MAX_DESCRIPTOR_TOKENS]


def _normalize_attribute_candidates(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    candidates: list[dict[str, object]] = []
    for index, item in enumerate(value[:_MAX_CANDIDATE_ATTRIBUTES]):
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain", "")).strip().lower()
        label = _slug(str(item.get("label", "")).strip())
        candidate_value = str(item.get("value", "")).strip()
        mutability = str(item.get("mutability", "evolving")).strip().lower()
        if domain not in _ATTRIBUTE_DOMAINS or not label or not candidate_value:
            continue
        if mutability not in _MUTABILITY_VALUES:
            mutability = "evolving"
        try:
            confidence = max(0.0, min(float(item.get("confidence", 0.6) or 0.6), 1.0))
        except (TypeError, ValueError):
            confidence = 0.6
        candidates.append(
            {
                "candidate_id": f"attribute_{index}_{label}",
                "domain": domain,
                "label": label,
                "value": candidate_value,
                "elaboration": str(item.get("elaboration")).strip()
                if item.get("elaboration") not in {None, ""}
                else None,
                "mutability": mutability,
                "confidence": confidence,
                "status": str(item.get("status", "pending")).strip().lower() or "pending",
            }
        )
    return candidates


def _normalize_preference_candidates(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    candidates: list[dict[str, object]] = []
    for index, item in enumerate(value[:_MAX_CANDIDATE_PREFERENCES]):
        if not isinstance(item, dict):
            continue
        category = _slug(str(item.get("category", "")).strip())
        subject = _slug(str(item.get("subject", "")).strip())
        signal = str(item.get("signal", "")).strip().lower()
        if not category or not subject or signal not in _PREFERENCE_SIGNALS:
            continue
        try:
            strength = int(item.get("strength", 3) or 3)
        except (TypeError, ValueError):
            strength = 3
        strength = max(1, min(strength, 5))
        summary = str(item.get("summary", "")).strip() or None
        candidates.append(
            {
                "candidate_id": f"preference_{index}_{category}_{subject}",
                "category": category,
                "subject": subject,
                "signal": signal,
                "strength": strength,
                "summary": summary,
                "status": str(item.get("status", "pending")).strip().lower() or "pending",
            }
        )
    return candidates


def _to_float(value: object, default: float) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: object, default: int) -> int:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_analysis_response(raw: str, artifact_id: str) -> ArtifactAnalysisResult:
    try:
        payload = json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError("Artifact analysis did not return valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Artifact analysis must return a JSON object.")

    content_kind = _slug(str(payload.get("content_kind", "")).strip() or "notes")
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise ValueError("Artifact analysis must include a summary.")
    return ArtifactAnalysisResult(
        artifact_id=artifact_id,
        content_kind=content_kind,
        summary=summary,
        descriptor_tokens=_normalize_descriptor_tokens(payload.get("descriptor_tokens")),
        candidate_attributes=_normalize_attribute_candidates(payload.get("candidate_attributes")),
        candidate_preferences=_normalize_preference_candidates(payload.get("candidate_preferences")),
        analyzed_at=datetime.now(UTC).isoformat(),
        analysis_method="model",
        analysis_warning=None,
    )


def _analysis_payload(
    result: ArtifactAnalysisResult,
    *,
    queued_at: str | None = None,
    started_at: str | None = None,
) -> dict[str, object]:
    status = "fallback_analyzed" if result.analysis_method == "heuristic_fallback" else "analyzed"
    return {
        "status": status,
        "content_kind": result.content_kind,
        "summary": result.summary,
        "descriptor_tokens": result.descriptor_tokens,
        "candidate_attributes": result.candidate_attributes,
        "candidate_preferences": result.candidate_preferences,
        "analyzed_at": result.analyzed_at,
        "analysis_method": result.analysis_method,
        "analysis_warning": result.analysis_warning,
        "queued_at": queued_at,
        "started_at": started_at,
        "completed_at": result.analyzed_at,
    }


def _tokenize_text(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _short_summary(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= _SUMMARY_SENTENCE_LIMIT:
        return compact
    return compact[: _SUMMARY_SENTENCE_LIMIT - 1].rstrip() + "..."


def _detect_content_kind(*, title: str, artifact_type: str, tags: list[str], content: str) -> str:
    haystack = " ".join([title, artifact_type, " ".join(tags), content[:2000]])
    if _RECIPE_HINT_RE.search(haystack):
        return "recipe_collection"
    if _JOURNAL_HINT_RE.search(haystack):
        return "journal"
    if _TRANSCRIPT_HINT_RE.search(haystack):
        return "transcript"
    if _READING_HINT_RE.search(haystack):
        return "reading_log"
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) >= 3 and sum(1 for line in lines[:8] if len(line.split()) <= 6) >= 3:
        return "list"
    return "notes"


def _descriptor_tokens(
    *,
    title: str,
    domain: str | None,
    tags: list[str],
    content_kind: str,
    content: str,
) -> list[str]:
    candidates: list[str] = [content_kind]
    if domain:
        candidates.append(domain)
    candidates.extend(tags)
    candidates.extend(token for token in _tokenize_text(title) if len(token) > 2)

    frequencies: dict[str, int] = {}
    for token in _tokenize_text(content):
        if len(token) <= 3:
            continue
        frequencies[token] = frequencies.get(token, 0) + 1
    top_terms = sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))
    candidates.extend(token for token, _count in top_terms[:8])
    return _normalize_descriptor_tokens(candidates)


def _fallback_candidate_attributes(
    *,
    title: str,
    domain: str | None,
    content_kind: str,
    content: str,
) -> list[dict[str, object]]:
    if domain not in _ATTRIBUTE_DOMAINS:
        return []
    if content_kind in {"recipe_collection", "reading_log", "list"}:
        return []
    if not _FIRST_PERSON_RE.search(content):
        return []
    excerpt = _short_summary(content.splitlines()[0] if content.splitlines() else content)
    label = _slug(title or f"{domain}_artifact_note")
    return [
        {
            "candidate_id": f"attribute_0_{label}",
            "domain": domain,
            "label": label,
            "value": excerpt,
            "elaboration": (
                "Generated by a deterministic local fallback because the richer local model analysis timed out."
            ),
            "mutability": "evolving",
            "confidence": 0.45,
            "status": "pending",
        }
    ]


def _build_fallback_analysis(
    artifact_id: str,
    artifact: dict[str, object],
    tags: list[str],
    *,
    warning: str,
) -> ArtifactAnalysisResult:
    title = str(artifact["title"])
    content = str(artifact["content"])
    domain = str(artifact["domain"]) if artifact["domain"] is not None else None
    artifact_type = str(artifact["type"])
    content_kind = _detect_content_kind(
        title=title,
        artifact_type=artifact_type,
        tags=tags,
        content=content,
    )
    summary = {
        "recipe_collection": "This upload appears to be a local collection of recipes or meal notes.",
        "journal": "This upload appears to be a reflective journal-style note.",
        "transcript": "This upload appears to be a conversation or transcript-style record.",
        "reading_log": "This upload appears to be a reading log or article list.",
        "list": "This upload appears to be a short list or inventory of items.",
        "notes": "This upload appears to be a general note or document.",
    }.get(content_kind, "This upload appears to be a general note or document.")
    if title.strip():
        summary = f"{summary} Title: {title.strip()}."
    return ArtifactAnalysisResult(
        artifact_id=artifact_id,
        content_kind=content_kind,
        summary=summary,
        descriptor_tokens=_descriptor_tokens(
            title=title,
            domain=domain,
            tags=tags,
            content_kind=content_kind,
            content=content,
        ),
        candidate_attributes=_fallback_candidate_attributes(
            title=title,
            domain=domain,
            content_kind=content_kind,
            content=content,
        ),
        candidate_preferences=[],
        analyzed_at=datetime.now(UTC).isoformat(),
        analysis_method="heuristic_fallback",
        analysis_warning=warning,
    )


def _call_provider(artifact_id: str, artifact: dict[str, object], tags: list[str], provider_config) -> ArtifactAnalysisResult:
    """Call the local model and return a result, falling back to heuristics on timeout."""
    messages = [
        {"role": "system", "content": _ANALYSIS_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "title": artifact["title"],
                    "domain": artifact["domain"],
                    "type": artifact["type"],
                    "source": artifact["source"],
                    "filename": cast(dict[str, object], artifact["metadata"]).get("filename"),
                    "tags": tags,
                    "content": artifact["content"],
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        },
    ]
    try:
        raw = PrivacyBroker(provider_config).extract_structured_attributes(
            messages,
            task_type="artifact_analysis",
            allow_external_input=False,
            timeout_seconds=_ARTIFACT_ANALYSIS_TIMEOUT_SECONDS,
        ).content
        assert isinstance(raw, str)
        return _parse_analysis_response(raw, artifact_id)
    except (requests.exceptions.Timeout, requests.exceptions.RequestException, ValueError) as exc:
        logger.warning(
            "Artifact analysis fell back to deterministic local heuristics for artifact %s: %s",
            artifact_id,
            exc,
        )
        return _build_fallback_analysis(artifact_id, artifact, tags, warning=_FALLBACK_WARNING)


def analyze_artifact(
    conn,
    artifact_id: str,
    provider_config,
) -> ArtifactAnalysisResult:
    """Analyze one stored artifact with a local model and persist the result in metadata."""
    if not getattr(provider_config, "is_local", False):
        raise ValueError("Artifact analysis requires a local provider.")

    artifact = get_artifact_record(conn, artifact_id)
    if artifact is None:
        raise ValueError("artifact not found")

    tags = get_artifact_tags(conn, artifact_id)
    result = _call_provider(artifact_id, artifact, tags, provider_config)
    metadata = dict(artifact["metadata"])
    metadata["analysis"] = _analysis_payload(result)
    update_artifact_metadata(conn, artifact_id, metadata)
    return result


def enqueue_artifact_analysis(conn, artifact_id: str) -> dict[str, object]:
    """Write queued status to metadata and return the status dict.

    Idempotent: if already queued, returns current state without updating queued_at.
    """
    artifact = get_artifact_record(conn, artifact_id)
    if artifact is None:
        raise ValueError("artifact not found")
    metadata = dict(artifact["metadata"])
    raw_analysis = metadata.get("analysis")
    existing: dict[str, object] = raw_analysis if isinstance(raw_analysis, dict) else {}
    if str(existing.get("status", "")) == "queued":
        return existing
    new_analysis: dict[str, object] = {
        **existing,
        "status": "queued",
        "queued_at": datetime.now(UTC).isoformat(),
        "started_at": None,
        "completed_at": None,
    }
    metadata["analysis"] = new_analysis
    update_artifact_metadata(conn, artifact_id, metadata)
    return new_analysis


def run_analysis_for_worker(artifact_id: str, llm_config: Any) -> None:
    """Execute analysis for one artifact in a background worker thread."""
    from config.llm_router import ConfigurationError
    from engine.setup_state import resolve_local_provider_config
    from server.db import get_db_connection

    with get_db_connection() as conn:
        artifact = get_artifact_record(conn, artifact_id)
        if artifact is None:
            logger.warning("Worker: artifact %s not found, skipping.", artifact_id)
            return
        metadata = dict(artifact["metadata"])
        raw_analysis = metadata.get("analysis")
        analysis: dict[str, object] = raw_analysis if isinstance(raw_analysis, dict) else {}
        status = str(analysis.get("status", "not_analyzed"))
        if status != "queued":
            logger.debug("Worker: artifact %s status is %r, skipping.", artifact_id, status)
            return
        _queued_at_raw = analysis.get("queued_at")
        queued_at: str | None = str(_queued_at_raw) if isinstance(_queued_at_raw, str) else None
        started_at = datetime.now(UTC).isoformat()
        metadata["analysis"] = {**analysis, "status": "running", "started_at": started_at}
        update_artifact_metadata(conn, artifact_id, metadata)

    try:
        provider_config = resolve_local_provider_config(llm_config)
    except ConfigurationError:
        logger.error("Worker: no local provider available for artifact %s.", artifact_id)
        _write_failed_status(artifact_id, queued_at, started_at, "no local provider available")
        return

    try:
        with get_db_connection() as conn:
            artifact = get_artifact_record(conn, artifact_id)
            if artifact is None:
                return
            tags = get_artifact_tags(conn, artifact_id)
        result = _call_provider(artifact_id, artifact, tags, provider_config)
        with get_db_connection() as conn:
            artifact = get_artifact_record(conn, artifact_id)
            if artifact is None:
                return
            metadata = dict(artifact["metadata"])
            metadata["analysis"] = _analysis_payload(result, queued_at=queued_at, started_at=started_at)
            update_artifact_metadata(conn, artifact_id, metadata)
    except Exception as exc:
        logger.exception("Worker: unexpected error analyzing artifact %s.", artifact_id)
        _write_failed_status(artifact_id, queued_at, started_at, f"unexpected error: {type(exc).__name__}")


def _write_failed_status(
    artifact_id: str,
    queued_at: object,
    started_at: object,
    reason: str,
) -> None:
    from server.db import get_db_connection

    try:
        with get_db_connection() as conn:
            artifact = get_artifact_record(conn, artifact_id)
            if artifact is None:
                return
            metadata = dict(artifact["metadata"])
            raw_analysis = metadata.get("analysis")
            existing: dict[str, object] = raw_analysis if isinstance(raw_analysis, dict) else {}
            failed_analysis: dict[str, object] = {
                **existing,
                "status": "failed",
                "analysis_warning": reason,
                "queued_at": queued_at,
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
            }
            metadata["analysis"] = failed_analysis
            update_artifact_metadata(conn, artifact_id, metadata)
    except Exception:
        logger.exception("Worker: failed to write failed status for artifact %s.", artifact_id)


def get_artifact_analysis(conn, artifact_id: str) -> dict[str, object] | None:
    """Return the persisted analysis payload for one artifact, or None if not found."""
    artifact = get_artifact_record(conn, artifact_id)
    if artifact is None:
        return None
    analysis = artifact["metadata"].get("analysis")
    if isinstance(analysis, dict):
        return analysis
    return None


def promote_artifact_analysis(
    conn,
    artifact_id: str,
    *,
    selected_attributes: list[dict[str, object]],
    selected_preferences: list[dict[str, object]],
) -> dict[str, object]:
    """Promote selected artifact-analysis candidates into canonical stores."""
    artifact = get_artifact_record(conn, artifact_id)
    if artifact is None:
        raise ValueError("artifact not found")

    metadata = dict(artifact["metadata"])
    analysis = metadata.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError("artifact has not been analyzed")

    cleaned_attributes = [
        {
            "domain": str(item["domain"]),
            "label": str(item["label"]),
            "value": str(item["value"]),
            "elaboration": item.get("elaboration"),
            "mutability": str(item.get("mutability", "evolving")),
            "confidence": _to_float(item.get("confidence", 0.6), 0.6),
        }
        for item in selected_attributes
        if item.get("domain") and item.get("label") and item.get("value")
    ]
    saved_attributes = save_preview_attributes(conn, cleaned_attributes) if cleaned_attributes else []

    preference_signal_ids: list[str] = []
    for item in selected_preferences:
        record = record_preference_signal(
            conn,
            PreferenceSignalInput(
                category=str(item["category"]),
                subject=str(item["subject"]),
                signal=str(item["signal"]),
                strength=_to_int(item.get("strength", 3), 3),
                source="explicit_feedback",
                context={
                    "artifact_id": artifact_id,
                    "artifact_title": str(artifact["title"]),
                    "promotion_source": "artifact_analysis",
                    "candidate_id": str(item.get("candidate_id", "")),
                },
            ),
        )
        preference_signal_ids.append(record.id)

    selected_attribute_ids = {str(item.get("candidate_id", "")) for item in selected_attributes}
    selected_preference_ids = {str(item.get("candidate_id", "")) for item in selected_preferences}

    updated_attribute_candidates: list[dict[str, object]] = []
    for candidate in analysis.get("candidate_attributes", []):
        if not isinstance(candidate, dict):
            continue
        updated = dict(candidate)
        candidate_id = str(updated.get("candidate_id", ""))
        if candidate_id in selected_attribute_ids:
            updated["status"] = "promoted"
        updated_attribute_candidates.append(updated)

    updated_preference_candidates: list[dict[str, object]] = []
    for candidate in analysis.get("candidate_preferences", []):
        if not isinstance(candidate, dict):
            continue
        updated = dict(candidate)
        candidate_id = str(updated.get("candidate_id", ""))
        if candidate_id in selected_preference_ids:
            updated["status"] = "promoted"
        updated_preference_candidates.append(updated)

    analysis["candidate_attributes"] = updated_attribute_candidates
    analysis["candidate_preferences"] = updated_preference_candidates
    analysis["last_promoted_at"] = datetime.now(UTC).isoformat()
    metadata["analysis"] = analysis
    update_artifact_metadata(conn, artifact_id, metadata)

    return {
        "artifact_id": artifact_id,
        "promoted_attribute_ids": [str(item.get("id")) for item in saved_attributes if item.get("id")],
        "promoted_preference_signal_ids": preference_signal_ids,
        "analysis": analysis,
    }
