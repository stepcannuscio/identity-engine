"""Deterministic candidate ranking against learned preferences."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import TypedDict

from engine.preference_summary import PreferenceSummaryItem, PreferenceSummaryPayload

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _normalize_token(token: str) -> str:
    normalized = token.lower()
    if len(normalized) > 4 and normalized.endswith("s"):
        return normalized[:-1]
    return normalized


def _tokenize(text: str) -> set[str]:
    return {_normalize_token(token) for token in _TOKEN_RE.findall(text.lower())}


class RankResult(TypedDict):
    """Typed candidate ranking result."""

    candidate: Mapping[str, object]
    label: str
    score: float
    reasons: list[str]


def _candidate_tokens(candidate: Mapping[str, object]) -> set[str]:
    parts: list[str] = []
    for key in ("name", "category", "description", "subject"):
        value = candidate.get(key)
        if value:
            parts.append(str(value))
    tags = candidate.get("tags", [])
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags)
    context_tags = candidate.get("context_tags", [])
    if isinstance(context_tags, list):
        parts.extend(str(tag) for tag in context_tags)
    return _tokenize(" ".join(parts))


def _preference_tokens(item: PreferenceSummaryItem) -> set[str]:
    return _tokenize(str(item.get("subject", "")))


def _string_list_value(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _base_weight(item: PreferenceSummaryItem) -> float:
    direction = str(item.get("direction", "positive"))
    source = str(item.get("source", "signal_summary"))
    status = str(item.get("status", ""))

    if direction == "negative":
        if source == "attribute" and status == "confirmed":
            return -3.0
        if source == "attribute":
            return -2.0
        return -2.0

    if source == "attribute" and status == "confirmed":
        return 3.0
    if source == "attribute":
        return 2.0
    return 1.0
def score_candidate_against_preferences(
    candidate: Mapping[str, object],
    preference_summary: PreferenceSummaryPayload,
    *,
    context_tag: str | None = None,
) -> RankResult:
    """Return a deterministic score plus explanation factors for one candidate."""
    candidate_tokens = _candidate_tokens(candidate)
    score = 0.0
    reasons: list[str] = []

    summary_items = list(preference_summary["positive"]) + list(
        preference_summary["negative"]
    )

    for item in summary_items:
        item_tokens = _preference_tokens(item)
        if not item_tokens or not candidate_tokens.intersection(item_tokens):
            continue

        weight = _base_weight(item)
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)):
            weight *= 1.0 + (min(float(confidence), 1.0) * 0.15)

        score += weight
        direction = str(item.get("direction", "positive"))
        subject = str(item.get("subject", item.get("summary", "preference"))).replace("_", " ")
        if direction == "negative":
            reasons.append(f"matched avoid preference: {subject}")
        else:
            reasons.append(f"matched positive preference: {subject}")

    if context_tag:
        context_tags = {
            tag.lower() for tag in _string_list_value(candidate.get("context_tags"))
        }
        if context_tag.lower() in context_tags:
            score += 1.0
            reasons.append(f"matched context tag: {context_tag}")

    label = str(candidate.get("name") or candidate.get("subject") or candidate.get("category") or "")
    return {
        "candidate": candidate,
        "label": label,
        "score": round(score, 2),
        "reasons": reasons,
    }


def rank_candidates(
    candidates: list[Mapping[str, object]],
    preference_summary: PreferenceSummaryPayload,
    *,
    context_tag: str | None = None,
) -> list[RankResult]:
    """Rank candidates by deterministic preference score."""
    scored = [
        score_candidate_against_preferences(
            candidate,
            preference_summary,
            context_tag=context_tag,
        )
        for candidate in candidates
    ]
    scored.sort(
        key=lambda item: (
            item["score"],
            str(item.get("label", "")),
        ),
        reverse=True,
    )
    return scored
