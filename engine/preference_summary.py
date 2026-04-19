"""Deterministic helpers for selecting relevant preference context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NotRequired, Required, TypedDict

from db.preference_signals import PreferenceSignalSummary, summarize_preference_signals
from engine.text_utils import tokenize

_PREFERENCE_LABEL_PREFIX = "preference_"

_SIMPLE_CAPS = {
    "max_attributes": 2,
    "max_signal_summaries": 2,
}

_OPEN_ENDED_CAPS = {
    "max_attributes": 4,
    "max_signal_summaries": 3,
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "for",
    "help",
    "i",
    "in",
    "is",
    "me",
    "my",
    "of",
    "on",
    "or",
    "should",
    "the",
    "to",
    "with",
}

_TASK_KEYWORDS = {
    "writing": {
        "brief",
        "communication",
        "concise",
        "draft",
        "email",
        "message",
        "response",
        "responses",
        "rewrite",
        "sound",
        "style",
        "tone",
        "voice",
        "write",
        "writing",
    },
    "planning": {
        "calendar",
        "focus",
        "habit",
        "habits",
        "morning",
        "plan",
        "planning",
        "priorities",
        "priority",
        "routine",
        "schedule",
        "task",
        "tasks",
        "workflow",
    },
    "recommendation": {
        "activity",
        "activities",
        "book",
        "books",
        "choose",
        "movie",
        "movies",
        "music",
        "option",
        "options",
        "pick",
        "recommend",
        "recommendation",
        "restaurant",
        "restaurants",
        "suggest",
        "travel",
        "trip",
    },
}

_DOMAIN_PROFILE_BOOSTS = {
    "writing": {"voice"},
    "planning": {"patterns"},
    "recommendation": set(),
}

_POSITIVE_TERMS = {"accept", "like", "prefer"}
_NEGATIVE_TERMS = {"avoid", "dislike", "reject"}

class PreferenceSummaryItem(TypedDict, total=False):
    """One bounded preference item used for prompting or ranking."""

    category: str
    subject: str
    direction: str
    summary: Required[str]
    source: Required[str]
    status: str
    confidence: float | None
    routing: str
    score: float
    observations: NotRequired[int]
    positive_count: NotRequired[int]
    negative_count: NotRequired[int]
    net_score: NotRequired[int]
    latest_at: NotRequired[str]


class PreferenceSummaryPayload(TypedDict):
    """Typed runtime preference summary payload."""

    task_profiles: list[str]
    positive: list[PreferenceSummaryItem]
    negative: list[PreferenceSummaryItem]


def empty_preference_summary() -> PreferenceSummaryPayload:
    """Return an empty typed preference summary."""
    return {
        "task_profiles": [],
        "positive": [],
        "negative": [],
    }


@dataclass(frozen=True)
class PreferenceContextResult:
    """Bounded preference context selected for one query."""

    attributes: list[dict]
    signal_items: list[PreferenceSummaryItem]
    summary: PreferenceSummaryPayload
    categories_used: list[str]
    item_count: int
    was_trimmed: bool
    budget_metadata: dict[str, int]


def is_preference_attribute(attribute: dict) -> bool:
    """Return True when an attribute represents learned preference state."""
    return str(attribute.get("label", "")).startswith(_PREFERENCE_LABEL_PREFIX)


def preference_budget_for_query_type(query_type: str) -> dict[str, int]:
    """Return deterministic preference caps by query type."""
    return _SIMPLE_CAPS if query_type == "simple" else _OPEN_ENDED_CAPS


def _tokenize(text: str) -> set[str]:
    return tokenize(text, stopwords=_STOPWORDS)


def _detect_task_profiles(query: str) -> list[str]:
    query_tokens = _tokenize(query)
    profiles: list[str] = []
    for profile, keywords in _TASK_KEYWORDS.items():
        if query_tokens.intersection(keywords):
            profiles.append(profile)
    return profiles


def _overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / max(len(left), len(right))


def _parse_timestamp(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _preference_recency(attribute: dict) -> float:
    last_confirmed = _parse_timestamp(attribute.get("last_confirmed"))
    updated_at = _parse_timestamp(attribute.get("updated_at"))
    now = datetime.now(UTC)

    if last_confirmed is not None:
        age_days = (now - last_confirmed).days
        return 0.08 if age_days <= 240 else 0.03 if age_days <= 540 else -0.03
    if updated_at is not None and (now - updated_at).days <= 120:
        return 0.03
    return 0.0


def _attribute_direction(attribute: dict) -> str:
    attr_tokens = _tokenize(
        " ".join(
            [
                str(attribute.get("label", "")),
                str(attribute.get("value", "")),
                str(attribute.get("elaboration", "")),
            ]
        )
    )
    if attr_tokens.intersection(_NEGATIVE_TERMS):
        return "negative"
    if attr_tokens.intersection(_POSITIVE_TERMS):
        return "positive"
    return "positive"


def _score_preference_attribute(
    query: str,
    task_profiles: list[str],
    attribute: dict,
    *,
    domain_hints: list[str] | None = None,
) -> float:
    query_tokens = _tokenize(query)
    attribute_tokens = _tokenize(
        " ".join(
            [
                str(attribute.get("label", "")),
                str(attribute.get("value", "")),
                str(attribute.get("elaboration", "")),
            ]
        )
    )
    lexical_overlap = _overlap_score(query_tokens, attribute_tokens)
    score = lexical_overlap * 0.45

    domain = str(attribute.get("domain", ""))
    profile_match = False
    for profile in task_profiles:
        if domain in _DOMAIN_PROFILE_BOOSTS.get(profile, set()):
            score += 0.3
            profile_match = True
            break

    if lexical_overlap == 0.0 and not profile_match:
        return 0.0

    if domain_hints and domain in domain_hints:
        score += 0.15

    status = str(attribute.get("status", ""))
    if status == "confirmed":
        score += 0.25
    elif status == "active":
        score += 0.1

    score += min(float(attribute.get("confidence", 0.0) or 0.0), 1.0) * 0.2
    score += _preference_recency(attribute)
    score -= min(int(attribute.get("prior_versions", 0) or 0) * 0.03, 0.09)
    return round(score, 4)


def _score_signal_summary(
    query: str,
    task_profiles: list[str],
    summary,
) -> float:
    query_tokens = _tokenize(query)
    summary_tokens = _tokenize(f"{summary.category} {summary.subject}")
    lexical_overlap = _overlap_score(query_tokens, summary_tokens)
    score = lexical_overlap * 0.45

    category_tokens = _tokenize(summary.category)
    profile_match = False
    for profile in task_profiles:
        if category_tokens.intersection(_TASK_KEYWORDS.get(profile, set())):
            score += 0.3
            profile_match = True
            break

    if lexical_overlap == 0.0 and not profile_match:
        return 0.0

    score += min(abs(int(summary.net_score)), 10) * 0.05
    score += min(int(summary.observations), 5) * 0.03
    return round(score, 4)


def _format_signal_summary(summary, direction: str) -> str:
    subject = summary.subject.replace("_", " ")
    if direction == "negative":
        return (
            f"Avoid {subject}. "
            f"Observed {summary.negative_count} negative and "
            f"{summary.positive_count} positive signals."
        )
    return (
        f"Prefer {subject}. "
        f"Observed {summary.positive_count} positive and {summary.negative_count} negative signals."
    )


def _attribute_covers_signal(attribute: dict, summary) -> bool:
    subject_tokens = _tokenize(summary.subject)
    if not subject_tokens:
        return False
    attribute_tokens = _tokenize(
        " ".join(
            [
                str(attribute.get("label", "")),
                str(attribute.get("value", "")),
                str(attribute.get("elaboration", "")),
            ]
        )
    )
    return subject_tokens.issubset(attribute_tokens)


def _build_attribute_summary_item(attribute: dict) -> PreferenceSummaryItem:
    return {
        "category": str(attribute.get("domain", "")),
        "subject": str(attribute.get("label", "")),
        "direction": _attribute_direction(attribute),
        "summary": str(attribute.get("value", "")),
        "source": "attribute",
        "status": str(attribute.get("status", "")),
        "confidence": float(attribute.get("confidence", 0.0) or 0.0),
        "routing": str(attribute.get("routing", "local_only")),
        "score": float(attribute.get("score", 0.0) or 0.0),
    }


def _build_signal_summary_item(
    summary: PreferenceSignalSummary,
    score: float,
    direction: str,
) -> PreferenceSummaryItem:
    return {
        "category": summary.category,
        "subject": summary.subject,
        "direction": direction,
        "summary": _format_signal_summary(summary, direction),
        "source": "signal_summary",
        "status": "summary",
        "confidence": None,
        "routing": "local_only",
        "score": score,
        "observations": int(summary.observations),
        "positive_count": int(summary.positive_count),
        "negative_count": int(summary.negative_count),
        "net_score": int(summary.net_score),
        "latest_at": str(summary.latest_at),
    }


def get_relevant_preference_context(
    query: str,
    query_type: str,
    conn,
    *,
    domain_hints: list[str] | None = None,
    intent_tags: list[str] | None = None,
) -> PreferenceContextResult:
    """Select bounded, task-sensitive preference context for one query."""
    budget = dict(preference_budget_for_query_type(query_type))
    if "voice_adaptation" in (intent_tags or []):
        budget["max_attributes"] += 2
        budget["max_signal_summaries"] += 1
    task_profiles = sorted(set(_detect_task_profiles(query) + list(intent_tags or [])))

    rows = conn.execute(
        """
        SELECT
            a.id,
            d.name,
            a.label,
            a.value,
            a.elaboration,
            a.confidence,
            a.routing,
            a.status,
            a.source,
            a.updated_at,
            a.last_confirmed,
            (
                SELECT COUNT(*)
                FROM attributes history
                WHERE history.domain_id = a.domain_id
                  AND history.label = a.label
                  AND history.status IN ('superseded', 'rejected', 'retracted')
            ) AS prior_versions
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE a.status IN ('active', 'confirmed') AND a.label LIKE 'preference_%'
        """
    ).fetchall()

    scored_attributes: list[dict] = []
    for row in rows:
        attribute = {
            "id": str(row[0]),
            "domain": str(row[1]),
            "label": str(row[2]),
            "value": str(row[3]),
            "elaboration": row[4],
            "confidence": float(row[5]),
            "routing": str(row[6]),
            "status": str(row[7]),
            "source": str(row[8]),
            "updated_at": row[9],
            "last_confirmed": row[10],
            "prior_versions": int(row[11] or 0),
        }
        attribute["score"] = _score_preference_attribute(
            query,
            task_profiles,
            attribute,
            domain_hints=domain_hints,
        )
        if attribute["score"] >= 0.35:
            scored_attributes.append(attribute)

    scored_attributes.sort(
        key=lambda item: (
            float(item["score"]),
            1 if item.get("status") == "confirmed" else 0,
            float(item.get("confidence", 0.0) or 0.0),
            str(item.get("label", "")),
        ),
        reverse=True,
    )
    selected_attributes = scored_attributes[: int(budget["max_attributes"])]

    signal_summaries = summarize_preference_signals(conn)
    scored_signal_items: list[tuple[float, PreferenceSignalSummary, str]] = []
    for summary in signal_summaries:
        score = _score_signal_summary(query, task_profiles, summary)
        if score < 0.35:
            continue

        direction = "positive"
        if summary.net_score < 0 or summary.negative_count > summary.positive_count:
            direction = "negative"
        if summary.net_score == 0 and summary.negative_count == summary.positive_count:
            continue

        if any(_attribute_covers_signal(attribute, summary) for attribute in selected_attributes):
            continue

        scored_signal_items.append((score, summary, direction))

    scored_signal_items.sort(
        key=lambda item: (
            item[0],
            abs(int(item[1].net_score)),
            str(item[1].latest_at),
            item[1].category,
            item[1].subject,
        ),
        reverse=True,
    )

    positive_items = [
        _build_attribute_summary_item(attribute)
        for attribute in selected_attributes
        if _attribute_direction(attribute) == "positive"
    ]
    negative_items = [
        _build_attribute_summary_item(attribute)
        for attribute in selected_attributes
        if _attribute_direction(attribute) == "negative"
    ]

    selected_signals = scored_signal_items[: int(budget["max_signal_summaries"])]
    for score, summary, direction in selected_signals:
        item = _build_signal_summary_item(summary, score, direction)
        if direction == "negative":
            negative_items.append(item)
        else:
            positive_items.append(item)

    categories_used = sorted(
        {
            str(item.get("category", ""))
            for item in positive_items + negative_items
            if item.get("category")
        }
    )

    return PreferenceContextResult(
        attributes=selected_attributes,
        signal_items=[
            _build_signal_summary_item(summary, score, direction)
            for score, summary, direction in selected_signals
        ],
        summary={
            "task_profiles": task_profiles,
            "positive": positive_items,
            "negative": negative_items,
        },
        categories_used=categories_used,
        item_count=len(selected_attributes) + len(selected_signals),
        was_trimmed=(
            len(scored_attributes) > len(selected_attributes)
            or len(scored_signal_items) > len(selected_signals)
        ),
        budget_metadata={
            "max_preference_attributes": int(budget["max_attributes"]),
            "max_preference_signal_summaries": int(budget["max_signal_summaries"]),
        },
    )
