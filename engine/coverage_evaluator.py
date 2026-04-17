"""Deterministic coverage and confidence evaluation for query inference.

The evaluator scores an already-assembled context against weighted caps,
classifies the result into one of four confidence labels, and emits
structured metadata so the rest of the pipeline can either hedge the
prompt or skip inference entirely when no grounded context exists.

This is a reasoning control layer, not an ML model. All thresholds are
explicit so the decision is reproducible and explainable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from engine.context_assembler import AssembledContext

ConfidenceLabel = Literal[
    "high_confidence",
    "medium_confidence",
    "low_confidence",
    "insufficient_data",
]

ATTRIBUTE_WEIGHT = 3
PREFERENCE_WEIGHT = 2
ARTIFACT_WEIGHT = 1

ATTRIBUTE_CAP = 5
PREFERENCE_CAP = 3
ARTIFACT_CAP = 3

CONFIRMED_BONUS = 1
HIGH_CONFIDENCE_ATTRIBUTE_BONUS = 1
HIGH_CONFIDENCE_THRESHOLD = 0.8

HIGH_SCORE_THRESHOLD = 7
MEDIUM_SCORE_THRESHOLD = 3

INSUFFICIENT_DATA_MESSAGE = (
    "I don't have enough grounded context to answer this confidently yet. "
    "Consider adding notes with `make capture`, running `make interview` to "
    "fill in core identity domains, or uploading a relevant artifact so future "
    "answers can reference it."
)


@dataclass(frozen=True)
class CoverageCounts:
    """Counts of each signal type that contributed to the assessment."""

    attributes: int
    preferences: int
    artifacts: int


@dataclass(frozen=True)
class CoverageAssessment:
    """Result of running the coverage evaluator on one assembled context."""

    counts: CoverageCounts
    score: float
    confidence: ConfidenceLabel
    notes: str | None


def _visible_preference_attributes(
    preference_attributes: list[dict],
    backend: str,
) -> list[dict]:
    if backend == "local":
        return preference_attributes
    return [
        attribute
        for attribute in preference_attributes
        if attribute.get("routing") != "local_only"
    ]


def _visible_artifact_chunks(artifact_chunks: list[dict], backend: str) -> list[dict]:
    if backend == "local":
        return artifact_chunks
    # Artifacts are local evidence only; external backends never see them.
    return []


def _classify(
    score: float,
    attribute_count: int,
    preference_count: int,
    artifact_count: int,
) -> ConfidenceLabel:
    if score >= HIGH_SCORE_THRESHOLD:
        return "high_confidence"
    if score >= MEDIUM_SCORE_THRESHOLD:
        return "medium_confidence"
    if attribute_count == 0 and preference_count == 0 and artifact_count == 0:
        return "insufficient_data"
    return "low_confidence"


def _notes_for(
    confidence: ConfidenceLabel,
    counts: CoverageCounts,
) -> str | None:
    if confidence == "insufficient_data":
        return (
            "No relevant attributes, preferences, or artifacts were retrieved."
        )
    if confidence == "low_confidence":
        return (
            f"Only thin context was available "
            f"(attributes={counts.attributes}, "
            f"preferences={counts.preferences}, "
            f"artifacts={counts.artifacts})."
        )
    if confidence == "medium_confidence":
        return "Partial context — some relevant signals, but coverage is uneven."
    return None


def evaluate_coverage(
    context: AssembledContext,
    *,
    backend: str = "local",
) -> CoverageAssessment:
    """Score an assembled context and classify answering confidence.

    The score only counts signals that the target backend will actually see,
    so local-only preferences and artifacts are excluded from an external
    assessment. This prevents over-counting context that the prompt builder
    will filter out later.
    """
    visible_preferences = _visible_preference_attributes(
        context.preference_attributes,
        backend,
    )
    visible_artifacts = _visible_artifact_chunks(context.artifact_chunks, backend)

    attribute_count = len(context.attributes)
    preference_count = len(visible_preferences)
    artifact_count = len(visible_artifacts)

    score = (
        ATTRIBUTE_WEIGHT * min(attribute_count, ATTRIBUTE_CAP)
        + PREFERENCE_WEIGHT * min(preference_count, PREFERENCE_CAP)
        + ARTIFACT_WEIGHT * min(artifact_count, ARTIFACT_CAP)
    )

    if any(
        str(attribute.get("status")) == "confirmed"
        for attribute in context.attributes
    ):
        score += CONFIRMED_BONUS

    if any(
        float(attribute.get("confidence", 0.0) or 0.0) >= HIGH_CONFIDENCE_THRESHOLD
        for attribute in context.attributes
    ):
        score += HIGH_CONFIDENCE_ATTRIBUTE_BONUS

    confidence = _classify(
        score=score,
        attribute_count=attribute_count,
        preference_count=preference_count,
        artifact_count=artifact_count,
    )

    counts = CoverageCounts(
        attributes=attribute_count,
        preferences=preference_count,
        artifacts=artifact_count,
    )

    return CoverageAssessment(
        counts=counts,
        score=float(score),
        confidence=confidence,
        notes=_notes_for(confidence, counts),
    )
