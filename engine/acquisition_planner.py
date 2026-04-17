"""Deterministic follow-up planning for targeted data acquisition."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from engine.context_assembler import AssembledContext
from engine.coverage_evaluator import CoverageAssessment
from engine.interview_catalog import DOMAIN_NAMES, get_first_question
from engine.retriever import DOMAIN_KEYWORDS

GapKind = Literal["identity", "preference", "artifact"]
SuggestionKind = Literal["quick_capture", "interview_question", "artifact_upload"]
PlanStatus = Literal["not_needed", "suggested"]

_STOPWORDS = {
    "a",
    "about",
    "am",
    "an",
    "and",
    "are",
    "be",
    "can",
    "do",
    "for",
    "how",
    "i",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "should",
    "tell",
    "the",
    "this",
    "to",
    "what",
    "when",
    "why",
    "with",
}
_TOKEN_RE = re.compile(r"[a-z0-9']+")
_STRONG_CONFIDENCE_THRESHOLD = 0.7
_MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class AcquisitionGap:
    """One missing context area that would improve future answers."""

    kind: GapKind
    reason: str
    domain: str | None = None


@dataclass(frozen=True)
class AcquisitionSuggestion:
    """One concrete next step the user can take."""

    kind: SuggestionKind
    prompt: str
    action: dict[str, str | int | float | bool | None]


@dataclass(frozen=True)
class AcquisitionPlan:
    """Structured follow-up plan included with query metadata."""

    status: PlanStatus
    gaps: list[AcquisitionGap]
    suggestions: list[AcquisitionSuggestion]


def empty_acquisition_plan() -> AcquisitionPlan:
    """Return an empty acquisition plan."""
    return AcquisitionPlan(status="not_needed", gaps=[], suggestions=[])


def _query_domains(query: str) -> list[str]:
    lowered = query.lower()
    matched: list[str] = []
    for domain, triggers in DOMAIN_KEYWORDS.items():
        if any(trigger in lowered for trigger in triggers):
            matched.append(domain)
    return matched


def _has_strong_identity_support(context: AssembledContext, domain: str) -> bool:
    for attribute in context.attributes:
        if attribute.get("domain") != domain:
            continue
        if str(attribute.get("status", "")) not in {"active", "confirmed"}:
            continue
        confidence = float(attribute.get("confidence", 0.0) or 0.0)
        if str(attribute.get("status")) == "confirmed" or confidence >= _STRONG_CONFIDENCE_THRESHOLD:
            return True
    return False


def _domain_is_effectively_empty(context: AssembledContext, domain: str) -> bool:
    return not any(attribute.get("domain") == domain for attribute in context.attributes)


def _has_relevant_preference_support(context: AssembledContext) -> bool:
    if context.preference_attributes:
        return True
    return bool(context.preference_summary["positive"] or context.preference_summary["negative"])


def _has_relevant_artifact_support(context: AssembledContext) -> bool:
    return bool(context.artifact_chunks)


def _should_plan_for_confidence(confidence: str) -> bool:
    return confidence in {"medium_confidence", "low_confidence", "insufficient_data"}


def _focus_phrase(query: str) -> str:
    tokens = [
        token
        for token in _TOKEN_RE.findall(query.lower())
        if token not in _STOPWORDS
    ]
    if not tokens:
        return "this topic"
    return " ".join(tokens[:4])


def _preference_category(context: AssembledContext) -> str:
    profiles = context.preference_summary.get("task_profiles", [])
    if profiles:
        return str(profiles[0])
    if context.source_profile == "preference_sensitive":
        return "general_preferences"
    return "general"


def _build_identity_suggestions(
    context: AssembledContext,
    domain: str,
) -> list[AcquisitionSuggestion]:
    suggestions = [
        AcquisitionSuggestion(
            kind="quick_capture",
            prompt=(
                f"I don't know much about your {domain} yet. Add a quick note so "
                "future answers are better grounded."
            ),
            action={
                "target": "attribute",
                "domain_hint": domain,
                "placeholder": f"Share a quick note about your {domain}.",
            },
        )
    ]

    first_question = get_first_question(domain)
    if domain in DOMAIN_NAMES and _domain_is_effectively_empty(context, domain) and first_question:
        suggestions.append(
            AcquisitionSuggestion(
                kind="interview_question",
                prompt=first_question,
                action={
                    "domain": domain,
                    "question": first_question,
                    "placeholder": "Answer in your own words.",
                },
            )
        )

    return suggestions


def _build_preference_suggestion(
    context: AssembledContext,
    query: str,
) -> AcquisitionSuggestion:
    category = _preference_category(context)
    subject = _focus_phrase(query).replace(" ", "_")
    return AcquisitionSuggestion(
        kind="quick_capture",
        prompt=(
            "I don't have enough preference signals for this kind of question yet. "
            "Add one quick preference."
        ),
        action={
            "target": "preference_signal",
            "category": category,
            "subject": subject,
            "signal": "prefer",
            "strength": 3,
            "placeholder": f"Example: prefer { _focus_phrase(query) }",
        },
    )


def _build_artifact_suggestion(query: str, domain: str | None) -> AcquisitionSuggestion:
    topic = _focus_phrase(query)
    return AcquisitionSuggestion(
        kind="artifact_upload",
        prompt="A note or file would help ground this answer better. Upload one if you have it.",
        action={
            "domain": domain,
            "title": f"Notes about {topic}",
            "type": "note",
            "source": "upload",
            "placeholder": "Paste a note here or choose a text file upload.",
        },
    )


def build_acquisition_plan(
    query: str,
    context: AssembledContext,
    coverage: CoverageAssessment,
) -> AcquisitionPlan:
    """Return deterministic follow-up actions for thin or uneven context."""
    if coverage.confidence == "high_confidence":
        return empty_acquisition_plan()
    if not _should_plan_for_confidence(coverage.confidence):
        return empty_acquisition_plan()

    matched_domains = _query_domains(query)
    gaps: list[AcquisitionGap] = []
    suggestions: list[AcquisitionSuggestion] = []

    if context.source_profile in {"self_question", "general"}:
        for domain in matched_domains:
            if _has_strong_identity_support(context, domain):
                continue
            gaps.append(
                AcquisitionGap(
                    kind="identity",
                    domain=domain,
                    reason="No strong current identity coverage was retrieved for this domain.",
                )
            )
            suggestions.extend(_build_identity_suggestions(context, domain))

    if context.source_profile == "preference_sensitive":
        if not _has_relevant_preference_support(context):
            gaps.append(
                AcquisitionGap(
                    kind="preference",
                    reason="No relevant preference signals or promoted preference attributes were retrieved.",
                )
            )
            suggestions.append(_build_preference_suggestion(context, query))

    if context.source_profile == "evidence_based":
        if not _has_relevant_artifact_support(context):
            domain = matched_domains[0] if matched_domains else None
            gaps.append(
                AcquisitionGap(
                    kind="artifact",
                    domain=domain,
                    reason="No relevant uploaded notes or artifacts were retrieved for this evidence-based question.",
                )
            )
            suggestions.append(_build_artifact_suggestion(query, domain))

    if coverage.confidence == "medium_confidence":
        required_gap_types = {gap.kind for gap in gaps}
        if not required_gap_types:
            return empty_acquisition_plan()

    if not gaps or not suggestions:
        return empty_acquisition_plan()

    return AcquisitionPlan(
        status="suggested",
        gaps=gaps[:_MAX_SUGGESTIONS],
        suggestions=suggestions[:_MAX_SUGGESTIONS],
    )
