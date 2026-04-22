"""Deterministic coverage and confidence evaluation for query inference.

The evaluator scores an already-assembled context against a 100-point style
model, classifies the result into one of four confidence labels, and emits
structured metadata so the rest of the pipeline can either hedge the prompt
or skip inference entirely when no grounded context exists.

Scoring model
-------------
  Attribute score  (cap 50): per-attribute quality scoring with confidence
                              modifiers; confirmed attributes score highest.
  Preference score (cap 25): tiered by type (confirmed attr, active attr,
                              signal cluster) plus a negative-signal bonus.
  Artifact score   (cap 20): scored by source diversity; single weak chunk
                              is penalised; multi-source agreement adds bonus.
  Consistency adj  (±5)    : +5 for multi-component agreement, −5 for a
                             strong preference split.

Classification bands (global defaults; per-profile overrides available)
-----------------------------------------------------------------------
  high_confidence   : score >= 65
  medium_confidence : 45 <= score < 65
  low_confidence    : 25 <= score < 45
  insufficient_data : score < 25

Guardrails are applied after scoring to prevent artifacts from overriding
identity absence and to block high confidence when evidence is too thin.

This is a reasoning control layer, not an ML model.  All thresholds and
weights are explicit constants so decisions are reproducible and explainable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from engine.context_assembler import AssembledContext
from engine.preference_summary import PreferenceSummaryPayload

ConfidenceLabel = Literal[
    "high_confidence",
    "medium_confidence",
    "low_confidence",
    "insufficient_data",
]

# ---------------------------------------------------------------------------
# Attribute scoring constants
# ---------------------------------------------------------------------------
ATTR_CONFIRMED_BASE = 12    # confirmed or refined
ATTR_ACTIVE_BASE = 10       # explicitly authored, active
ATTR_INFERRED_BASE = 6      # inferred / system-generated, active
ATTR_HIGH_CONF_BONUS = 2    # confidence >= 0.85
ATTR_MED_CONF_BONUS = 1     # confidence 0.70–0.84
ATTR_LOW_CONF_PENALTY = -2  # confidence < 0.60
ATTR_SCORE_CAP = 50

# ---------------------------------------------------------------------------
# Preference scoring constants
# ---------------------------------------------------------------------------
PREF_CONFIRMED_ATTR = 8
PREF_ACTIVE_ATTR = 5
PREF_STRONG_SIGNAL = 4      # abs(net_score) >= 3 and observations >= 3
PREF_WEAK_SIGNAL = 2
PREF_NEGATIVE_BONUS = 3     # knowing what to avoid is confidence too
PREF_SCORE_CAP = 25

# ---------------------------------------------------------------------------
# Artifact scoring constants
# ---------------------------------------------------------------------------
ARTIFACT_FIRST_CHUNK = 4     # first chunk per distinct source
ARTIFACT_EXTRA_CHUNK = 1     # additional chunks from the same source
ARTIFACT_SECOND_SOURCE = 4   # bonus for the second distinct source
ARTIFACT_THIRD_SOURCE = 3    # bonus for the third distinct source
ARTIFACT_MULTI_SOURCE_BONUS = 3  # multi-source agreement bonus
ARTIFACT_WEAK_PENALTY = -2   # single weak / short / ambiguous chunk
ARTIFACT_SCORE_CAP = 20

# ---------------------------------------------------------------------------
# Consistency adjustment constants
# ---------------------------------------------------------------------------
CONSISTENCY_MULTI_SIGNAL_BONUS = 5  # all three components contribute
CONSISTENCY_SPLIT_PENALTY = -5      # strong conflicting preference signals
CONSISTENCY_CONFIRMED_ALIGNMENT_BONUS = 3
STALE_ATTRIBUTE_PENALTY = -4
MOSTLY_INFERRED_PENALTY = -4
STALE_UPDATE_DAYS = 365

# ---------------------------------------------------------------------------
# Classification thresholds — global defaults
# ---------------------------------------------------------------------------
HIGH_SCORE_THRESHOLD = 65
MEDIUM_SCORE_THRESHOLD = 45
LOW_SCORE_THRESHOLD = 25

# Per-profile thresholds: (high, medium, low, insufficient)
_PROFILE_THRESHOLDS: dict[str, tuple[int, int, int, int]] = {
    "general":              (65, 45, 25, 25),
    "preference_sensitive": (60, 42, 25, 25),
    "voice_generation":     (62, 44, 25, 25),
    "self_question":        (70, 50, 30, 30),
    "artifact_grounded_self": (60, 35, 20, 20),
    "evidence_based":       (60, 40, 20, 20),
}

_INFERRED_SOURCES = frozenset({"inferred", "system_inference"})

INSUFFICIENT_DATA_MESSAGE = (
    "I don't have enough grounded context to answer this confidently yet. "
    "Consider adding notes with `make capture`, running `make interview` to "
    "fill in core identity domains, or uploading a relevant artifact so future "
    "answers can reference it."
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverageCounts:
    """Counts of each signal type that contributed to the assessment."""

    attributes: int
    preferences: int
    artifacts: int


@dataclass(frozen=True)
class ScoreBreakdown:
    """Component scores for testing and future calibration.

    Not surfaced directly in the public API; available on CoverageAssessment
    for tests and internal pipeline consumers.
    """

    attribute_score: float
    preference_score: float
    artifact_score: float
    consistency_adjustment: float
    total_score: float
    cap_applied: str | None
    query_type_profile: str


@dataclass(frozen=True)
class CoverageAssessment:
    """Result of running the coverage evaluator on one assembled context."""

    counts: CoverageCounts
    score: float            # equals breakdown.total_score
    confidence: ConfidenceLabel
    notes: str | None
    breakdown: ScoreBreakdown


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def _attribute_base(attribute: dict) -> int:
    status = str(attribute.get("status", ""))
    if status == "confirmed":
        return ATTR_CONFIRMED_BASE
    source = str(attribute.get("source", ""))
    if source in _INFERRED_SOURCES:
        return ATTR_INFERRED_BASE
    return ATTR_ACTIVE_BASE


def _confidence_modifier(attribute: dict) -> int:
    conf = float(attribute.get("confidence", 0.0) or 0.0)
    if conf >= 0.85:
        return ATTR_HIGH_CONF_BONUS
    if conf >= 0.70:
        return ATTR_MED_CONF_BONUS
    if conf < 0.60:
        return ATTR_LOW_CONF_PENALTY
    return 0


def _score_attributes(attributes: list[dict], backend: str) -> float:
    visible = [
        a for a in attributes
        if backend == "local" or a.get("routing") != "local_only"
    ]
    total = 0
    for attr in visible:
        status = str(attr.get("status", ""))
        if status in {"rejected", "superseded", "retracted"}:
            continue
        total += _attribute_base(attr) + _confidence_modifier(attr)
    return float(min(total, ATTR_SCORE_CAP))


def _pref_has_identity_support(preference_attributes: list[dict], backend: str) -> bool:
    """Return True when at least one confirmed or active preference attribute is visible."""
    for attr in preference_attributes:
        if backend != "local" and attr.get("routing") == "local_only":
            continue
        status = str(attr.get("status", ""))
        if status in {"confirmed", "active"}:
            return True
    return False


def _score_preferences(
    preference_attributes: list[dict],
    preference_summary: PreferenceSummaryPayload,
    backend: str,
) -> tuple[float, bool]:
    """Return (preference_score, has_confirmed_or_promoted_support)."""
    total = 0
    has_attr_support = False

    for attr in preference_attributes:
        if backend != "local" and attr.get("routing") == "local_only":
            continue
        status = str(attr.get("status", ""))
        if status == "confirmed":
            total += PREF_CONFIRMED_ATTR
            has_attr_support = True
        elif status == "active":
            total += PREF_ACTIVE_ATTR
            has_attr_support = True

    # Score signal-summary items from the preference_summary payload.
    # Signal summaries have source == "signal_summary" and carry observation counts.
    for direction_key, items in (
        ("positive", preference_summary["positive"]),
        ("negative", preference_summary["negative"]),
    ):
        for item in items:
            if item.get("source") != "signal_summary":
                continue
            if backend != "local" and item.get("routing") == "local_only":
                continue
            observations = int(item.get("observations", 0) or 0)
            net_score = int(item.get("net_score", 0) or 0)
            if abs(net_score) >= 3 and observations >= 3:
                total += PREF_STRONG_SIGNAL
            else:
                total += PREF_WEAK_SIGNAL
            if direction_key == "negative":
                total += PREF_NEGATIVE_BONUS

    return float(min(total, PREF_SCORE_CAP)), has_attr_support


def _score_artifacts(
    artifact_chunks: list[dict],
    artifact_sources: list[str],
    backend: str,
) -> float:
    if backend != "local":
        return 0.0

    if not artifact_chunks:
        return 0.0

    # Count chunks per source title.
    source_counts: dict[str, int] = {}
    for chunk in artifact_chunks:
        title = str(chunk.get("title", "")).strip() or "_unknown"
        source_counts[title] = source_counts.get(title, 0) + 1

    distinct_sources = len(source_counts)
    total = 0

    # First chunk per source: ARTIFACT_FIRST_CHUNK; extras: ARTIFACT_EXTRA_CHUNK.
    for count in source_counts.values():
        total += ARTIFACT_FIRST_CHUNK + (count - 1) * ARTIFACT_EXTRA_CHUNK

    # Bonus for second and third distinct sources.
    if distinct_sources >= 2:
        total += ARTIFACT_SECOND_SOURCE
    if distinct_sources >= 3:
        total += ARTIFACT_THIRD_SOURCE

    # Multi-source agreement bonus.
    if distinct_sources >= 2:
        total += ARTIFACT_MULTI_SOURCE_BONUS

    # Weak-chunk penalty: single chunk with very short content.
    if len(artifact_chunks) == 1:
        content = str(artifact_chunks[0].get("content", "")).strip()
        if len(content) < 20:
            total += ARTIFACT_WEAK_PENALTY

    return float(min(max(total, 0), ARTIFACT_SCORE_CAP))


def _score_consistency(
    attribute_score: float,
    preference_score: float,
    artifact_score: float,
    attributes: list[dict],
    preference_attributes: list[dict],
    preference_summary: PreferenceSummaryPayload,
) -> float:
    """Return a small consistency adjustment.

    Adds a bonus when all three evidence types contribute, and penalises when
    preference signals show a strong unresolved split.

    Full correction-awareness (e.g. subtracting for recently rejected beliefs)
    requires passing rejected-attribute history, which is not yet available in
    the assembled context.  That is noted as a future extension.
    """
    adjustment = 0

    if attribute_score > 0 and preference_score > 0 and artifact_score > 0:
        adjustment += CONSISTENCY_MULTI_SIGNAL_BONUS

    has_confirmed_identity = any(str(a.get("status", "")) == "confirmed" for a in attributes)
    has_confirmed_preference = any(
        str(a.get("status", "")) == "confirmed" for a in preference_attributes
    )
    if has_confirmed_identity and has_confirmed_preference:
        adjustment += CONSISTENCY_CONFIRMED_ALIGNMENT_BONUS

    positive_items = preference_summary["positive"]
    negative_items = preference_summary["negative"]
    if positive_items and negative_items:
        positive_signal = any(
            abs(int(i.get("net_score", 0) or 0)) >= 3 for i in positive_items
        )
        negative_signal = any(
            abs(int(i.get("net_score", 0) or 0)) >= 3 for i in negative_items
        )
        if positive_signal and negative_signal:
            adjustment += CONSISTENCY_SPLIT_PENALTY

    return float(adjustment)


def _recency_penalty(attributes: list[dict]) -> float:
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

    stale = 0
    inferred = 0
    total = 0
    now = datetime.now(UTC)
    for attribute in attributes:
        total += 1
        if str(attribute.get("source", "")) in _INFERRED_SOURCES:
            inferred += 1
        last_confirmed = attribute.get("last_confirmed")
        updated_at = attribute.get("updated_at")
        updated_at_dt = _parse_timestamp(updated_at)
        if (
            last_confirmed in {None, ""}
            and updated_at_dt is not None
            and (now - updated_at_dt).days >= STALE_UPDATE_DAYS
        ):
            stale += 1

    penalty = 0.0
    if total and stale == total:
        penalty += STALE_ATTRIBUTE_PENALTY
    if total >= 2 and inferred / total >= 0.6:
        penalty += MOSTLY_INFERRED_PENALTY
    return penalty


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def _apply_guardrails(
    score: float,
    source_profile: str,
    attribute_score: float,
    has_pref_attr_support: bool,
    artifact_score: float,
    artifact_sources: list[str],
    profile_thresholds: tuple[int, int, int, int],
) -> tuple[float, str | None]:
    """Enforce structural guardrails and return (adjusted_score, cap_label)."""
    high_t, medium_t, _low_t, _insuf_t = profile_thresholds
    has_identity_support = attribute_score > 0 or has_pref_attr_support

    # Guardrail 1: no high confidence without identity support.
    if source_profile in {"self_question", "general"} and not has_identity_support:
        cap = float(high_t - 1)
        if score > cap:
            return cap, "no_identity_support"

    # Guardrail 2: evidence-based queries can lean on artifacts more, but only
    # when the evidence is meaningfully grounded.
    if source_profile in {"evidence_based", "artifact_grounded_self"} and artifact_score > 0:
        strong_single_source = artifact_score >= 8.0 and has_identity_support
        if len(artifact_sources) < 2 and not strong_single_source:
            high_cap = float(high_t - 1)
            if score > high_cap:
                return high_cap, "artifact_single_source_below_high"
        return score, None

    # Guardrail 3: outside evidence-based queries, artifact-only evidence stays
    # capped below medium to prevent over-reliance on notes or uploads.
    if not has_identity_support and artifact_score > 0:
        if len(artifact_sources) < 2:
            medium_cap = float(medium_t - 1)
            if score > medium_cap:
                return medium_cap, "artifact_only_single_source"

    return score, None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(
    score: float,
    attribute_count: int,
    preference_count: int,
    artifact_count: int,
    thresholds: tuple[int, int, int, int],
) -> ConfidenceLabel:
    high_t, medium_t, low_t, insuf_t = thresholds

    if score >= high_t:
        return "high_confidence"
    if score >= medium_t:
        return "medium_confidence"
    if attribute_count == 0 and preference_count == 0 and artifact_count == 0:
        return "insufficient_data"
    if score < insuf_t:
        return "insufficient_data"
    if score >= low_t:
        return "low_confidence"
    return "insufficient_data"


def _notes_for(
    confidence: ConfidenceLabel,
    counts: CoverageCounts,
    cap_applied: str | None,
    feedback_gap_note: str | None = None,
    shift_cluster_note: str | None = None,
) -> str | None:
    note: str | None = None
    if confidence == "insufficient_data":
        note = "No relevant attributes, preferences, or artifacts were retrieved."
    elif cap_applied == "no_identity_support":
        note = (
            "Artifact evidence present but no active identity attributes or "
            "preference attributes found — capped below high confidence."
        )
    elif cap_applied == "artifact_single_source_below_high":
        note = (
            "Evidence relies on a single artifact source without enough "
            "structured support — capped below high confidence."
        )
    elif cap_applied == "artifact_only_single_source":
        note = (
            "Evidence comes from a single artifact source only — "
            "capped below medium confidence."
        )
    elif confidence == "low_confidence":
        note = (
            f"Only thin context was available "
            f"(attributes={counts.attributes}, "
            f"preferences={counts.preferences}, "
            f"artifacts={counts.artifacts})."
        )
    elif confidence == "medium_confidence":
        note = "Partial context — some relevant signals, but coverage is uneven."

    if confidence == "low_confidence" and feedback_gap_note:
        if note:
            note = f"{note} {feedback_gap_note}"
        else:
            note = feedback_gap_note

    if shift_cluster_note:
        if note:
            return f"{note} {shift_cluster_note}"
        return shift_cluster_note
    return note


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def evaluate_coverage(
    context: AssembledContext,
    *,
    backend: str = "local",
    feedback_gap_note: str | None = None,
    shift_cluster_note: str | None = None,
) -> CoverageAssessment:
    """Score an assembled context and classify answering confidence.

    The score only counts signals that the target backend will actually see,
    so local-only preferences and artifacts are always excluded from an
    external assessment.

    Returns a CoverageAssessment that includes a ScoreBreakdown with component
    scores for testing and future calibration.  The breakdown is not surfaced
    in the public API response; only confidence, counts, and notes are.
    """
    profile = context.source_profile
    thresholds = _PROFILE_THRESHOLDS.get(profile, _PROFILE_THRESHOLDS["general"])
    if "planning" in context.intent_tags:
        high_t, medium_t, low_t, insuf_t = thresholds
        thresholds = (max(high_t - 2, medium_t), max(medium_t - 2, low_t), low_t, insuf_t)

    attribute_score = _score_attributes(context.attributes, backend)
    preference_score, has_pref_attr_support = _score_preferences(
        context.preference_attributes,
        context.preference_summary,
        backend,
    )
    artifact_score = _score_artifacts(
        context.artifact_chunks,
        context.artifact_sources,
        backend,
    )
    consistency_adj = _score_consistency(
        attribute_score,
        preference_score,
        artifact_score,
        context.attributes,
        context.preference_attributes,
        context.preference_summary,
    )
    consistency_adj += _recency_penalty(context.attributes)
    raw_score = (
        attribute_score
        + preference_score
        + artifact_score
        + consistency_adj
    )

    raw_score, cap_applied = _apply_guardrails(
        raw_score,
        profile,
        attribute_score,
        has_pref_attr_support,
        artifact_score,
        context.artifact_sources,
        thresholds,
    )

    # Visible counts for the API (backend-filtered).
    visible_attributes = [
        a for a in context.attributes
        if backend == "local" or a.get("routing") != "local_only"
    ]
    visible_preferences = [
        a for a in context.preference_attributes
        if backend == "local" or a.get("routing") != "local_only"
    ]
    visible_artifacts = context.artifact_chunks if backend == "local" else []

    attribute_count = len(visible_attributes)
    preference_count = len(visible_preferences)
    artifact_count = len(visible_artifacts)

    confidence = _classify(
        raw_score,
        attribute_count,
        preference_count,
        artifact_count,
        thresholds,
    )

    counts = CoverageCounts(
        attributes=attribute_count,
        preferences=preference_count,
        artifacts=artifact_count,
    )
    breakdown = ScoreBreakdown(
        attribute_score=attribute_score,
        preference_score=preference_score,
        artifact_score=artifact_score,
        consistency_adjustment=consistency_adj,
        total_score=raw_score,
        cap_applied=cap_applied,
        query_type_profile=profile,
    )

    return CoverageAssessment(
        counts=counts,
        score=raw_score,
        confidence=confidence,
        notes=_notes_for(
            confidence,
            counts,
            cap_applied,
            feedback_gap_note=feedback_gap_note,
            shift_cluster_note=shift_cluster_note,
        ),
        breakdown=breakdown,
    )
