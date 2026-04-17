"""Tests for engine/coverage_evaluator.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.context_assembler import AssembledContext
from engine.coverage_evaluator import (
    ATTR_SCORE_CAP,
    HIGH_SCORE_THRESHOLD,
    PREF_ACTIVE_ATTR,
    evaluate_coverage,
)
from typing import cast

from engine.preference_summary import PreferenceSummaryPayload, empty_preference_summary


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _context(
    attributes: list[dict] | None = None,
    preference_attributes: list[dict] | None = None,
    artifact_chunks: list[dict] | None = None,
    artifact_sources: list[str] | None = None,
    retrieval_mode: str = "simple",
    preference_summary: PreferenceSummaryPayload | None = None,
) -> AssembledContext:
    attributes = attributes or []
    preference_attributes = preference_attributes or []
    artifact_chunks = artifact_chunks or []
    artifact_sources = artifact_sources or sorted(
        {str(c.get("title", "")).strip() for c in artifact_chunks if c.get("title")}
    )
    pref_summary = (
        preference_summary if preference_summary is not None else empty_preference_summary()
    )
    return AssembledContext(
        task_type="query",
        input_text="test",
        attributes=attributes,
        session_history=[],
        domains_used=[],
        attribute_count=len(attributes),
        retrieval_mode=retrieval_mode,
        was_trimmed=False,
        contains_local_only=False,
        preference_attributes=preference_attributes,
        preference_count=len(preference_attributes),
        artifact_chunks=artifact_chunks,
        artifact_count=len(artifact_chunks),
        artifact_sources=artifact_sources,
        preference_summary=pref_summary,
    )


def _active_attr(
    confidence: float = 0.75,
    routing: str = "external_ok",
    status: str = "active",
    source: str = "manual",
) -> dict:
    return {
        "domain": "goals",
        "label": "priority",
        "value": "Ship project",
        "status": status,
        "confidence": confidence,
        "routing": routing,
        "source": source,
    }


def _confirmed_attr(confidence: float = 0.90) -> dict:
    return _active_attr(confidence=confidence, status="confirmed")


# ---------------------------------------------------------------------------
# Empty context
# ---------------------------------------------------------------------------

def test_empty_context_is_insufficient_data():
    assessment = evaluate_coverage(_context(), backend="local")

    assert assessment.confidence == "insufficient_data"
    assert assessment.counts.attributes == 0
    assert assessment.counts.preferences == 0
    assert assessment.counts.artifacts == 0
    assert assessment.score == 0.0
    assert assessment.notes is not None


# ---------------------------------------------------------------------------
# Score composition — attributes
# ---------------------------------------------------------------------------

def test_single_active_attribute_scores_correctly_but_is_insufficient():
    # active (10) + mid-confidence modifier (1) = 11; 11 < 25 → insufficient_data.
    context = _context(attributes=[_active_attr(confidence=0.75)])
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.confidence == "insufficient_data"
    assert assessment.breakdown.attribute_score == 11.0


def test_three_active_attributes_reach_low_confidence():
    # 3 × (10 + 1) = 33; 25 ≤ 33 < 45 → low_confidence.
    attrs = [_active_attr(confidence=0.75) for _ in range(3)]
    context = _context(attributes=attrs)
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.confidence == "low_confidence"


def test_confirmed_attribute_scores_higher_than_active():
    active_ctx = _context(attributes=[_active_attr(confidence=0.75)])
    confirmed_ctx = _context(attributes=[_confirmed_attr(confidence=0.75)])

    assert (
        evaluate_coverage(confirmed_ctx, backend="local").breakdown.attribute_score
        > evaluate_coverage(active_ctx, backend="local").breakdown.attribute_score
    )


def test_inferred_attribute_scores_lower_than_active():
    inferred_ctx = _context(attributes=[_active_attr(confidence=0.75, source="inferred")])
    explicit_ctx = _context(attributes=[_active_attr(confidence=0.75, source="manual")])

    assert (
        evaluate_coverage(inferred_ctx, backend="local").breakdown.attribute_score
        < evaluate_coverage(explicit_ctx, backend="local").breakdown.attribute_score
    )


def test_high_confidence_attribute_adds_bonus():
    low_conf_ctx = _context(attributes=[_active_attr(confidence=0.55)])
    high_conf_ctx = _context(attributes=[_active_attr(confidence=0.90)])

    assert (
        evaluate_coverage(high_conf_ctx, backend="local").breakdown.attribute_score
        > evaluate_coverage(low_conf_ctx, backend="local").breakdown.attribute_score
    )


def test_attribute_score_capped_at_50():
    # Each confirmed high-conf attr = 12 + 2 = 14; 4 × 14 = 56 > cap 50.
    many = [_confirmed_attr(confidence=0.90) for _ in range(4)]
    assessment = evaluate_coverage(_context(attributes=many), backend="local")

    assert assessment.breakdown.attribute_score == ATTR_SCORE_CAP


def test_confirmed_plus_high_confidence_five_attrs_hits_score_cap():
    # 5 × (12 + 2) = 70, capped at ATTR_SCORE_CAP (50).
    # 50 pts alone → medium_confidence (≥ 45, < 65).
    # High (65) requires additional signals from preferences or artifacts.
    attrs = [_confirmed_attr(confidence=0.90) for _ in range(5)]
    assessment = evaluate_coverage(_context(attributes=attrs), backend="local")

    assert assessment.breakdown.attribute_score == ATTR_SCORE_CAP
    assert assessment.confidence == "medium_confidence"


def test_single_weak_inferred_attribute_cannot_reach_medium():
    # inferred (6) + low-conf penalty (−2) = 4 → insufficient (< 25).
    context = _context(attributes=[_active_attr(confidence=0.55, source="inferred")])
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.confidence == "insufficient_data"


# ---------------------------------------------------------------------------
# Score composition — preferences
# ---------------------------------------------------------------------------

def test_preference_attributes_contribute_to_score():
    pref = {
        "label": "preference_writing_style_concise",
        "value": "I prefer concise responses",
        "routing": "local_only",
        "status": "active",
    }
    assessment = evaluate_coverage(
        _context(preference_attributes=[pref]), backend="local"
    )

    assert assessment.breakdown.preference_score == float(PREF_ACTIVE_ATTR)


def test_confirmed_preference_attr_scores_higher_than_active():
    active_ctx = _context(
        preference_attributes=[
            {"label": "preference_x", "value": "x", "routing": "local_only", "status": "active"}
        ]
    )
    confirmed_ctx = _context(
        preference_attributes=[
            {"label": "preference_x", "value": "x", "routing": "local_only", "status": "confirmed"}
        ]
    )

    assert (
        evaluate_coverage(confirmed_ctx, backend="local").breakdown.preference_score
        > evaluate_coverage(active_ctx, backend="local").breakdown.preference_score
    )


def test_preference_signal_cluster_scores_correctly():
    # Strong signal cluster in preference_summary.
    pref_summary = cast(PreferenceSummaryPayload, {
        "task_profiles": [],
        "positive": [
            {
                "source": "signal_summary",
                "routing": "local_only",
                "direction": "positive",
                "summary": "Prefer concise",
                "observations": 5,
                "net_score": 4,
                "positive_count": 5,
                "negative_count": 0,
            }
        ],
        "negative": [],
    })
    assessment = evaluate_coverage(
        _context(preference_summary=pref_summary), backend="local"
    )

    assert assessment.breakdown.preference_score == 4.0  # PREF_STRONG_SIGNAL


def test_negative_preference_signal_adds_bonus():
    pref_summary = cast(PreferenceSummaryPayload, {
        "task_profiles": [],
        "positive": [],
        "negative": [
            {
                "source": "signal_summary",
                "routing": "local_only",
                "direction": "negative",
                "summary": "Avoid verbose",
                "observations": 4,
                "net_score": -4,
                "positive_count": 0,
                "negative_count": 4,
            }
        ],
    })
    assessment = evaluate_coverage(
        _context(preference_summary=pref_summary), backend="local"
    )

    # PREF_STRONG_SIGNAL (4) + PREF_NEGATIVE_BONUS (3) = 7.
    assert assessment.breakdown.preference_score == 7.0


# ---------------------------------------------------------------------------
# Score composition — artifacts
# ---------------------------------------------------------------------------

def _chunk(title: str, content: str = "some content here") -> dict:
    return {"title": title, "content": content, "chunk_index": 0, "routing": "local_only"}


def test_single_artifact_chunk_gives_nonzero_score():
    assessment = evaluate_coverage(_context(artifact_chunks=[_chunk("notes")]), backend="local")

    assert assessment.breakdown.artifact_score > 0


def test_single_weak_artifact_chunk_alone_is_insufficient():
    # 4 pts artifact score < 25 (default insufficient threshold).
    assessment = evaluate_coverage(_context(artifact_chunks=[_chunk("notes")]), backend="local")

    assert assessment.confidence == "insufficient_data"


def test_two_distinct_artifact_sources_score_higher():
    chunks_one = [_chunk("doc_a")]
    chunks_two = [_chunk("doc_a"), _chunk("doc_b")]

    score_one = (
        evaluate_coverage(_context(artifact_chunks=chunks_one), backend="local")
        .breakdown.artifact_score
    )
    score_two = (
        evaluate_coverage(_context(artifact_chunks=chunks_two), backend="local")
        .breakdown.artifact_score
    )

    assert score_two > score_one


def test_artifact_score_external_backend_is_zero():
    chunk = {"title": "notes", "content": "content", "chunk_index": 0, "routing": "local_only"}
    assessment = evaluate_coverage(_context(artifact_chunks=[chunk]), backend="external")

    assert assessment.breakdown.artifact_score == 0.0


# ---------------------------------------------------------------------------
# Rich context reaches high confidence
# ---------------------------------------------------------------------------

def test_rich_context_reaches_high_confidence():
    # 5 confirmed attrs (12 + 2 each = 70, capped at 50)
    # + 2 confirmed preference attrs (8 each = 16, capped at 25 → 16)
    # + 2 artifact sources (4+4+3 = 11, capped at 20 → 11)
    # + consistency multi-signal bonus (5)
    # Total: 50 + 16 + 11 + 5 = 82 → high_confidence.
    attrs = [_confirmed_attr(confidence=0.90) for _ in range(5)]
    prefs = [
        {"label": f"preference_{i}", "value": "v", "routing": "local_only", "status": "confirmed"}
        for i in range(2)
    ]
    chunks = [
        {"title": "doc_a", "content": "content a", "chunk_index": 0, "routing": "local_only"},
        {"title": "doc_b", "content": "content b", "chunk_index": 0, "routing": "local_only"},
    ]
    context = _context(attributes=attrs, preference_attributes=prefs, artifact_chunks=chunks)
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.confidence == "high_confidence"
    assert assessment.score >= HIGH_SCORE_THRESHOLD


# ---------------------------------------------------------------------------
# External backend drops local-only signals
# ---------------------------------------------------------------------------

def test_external_backend_drops_local_only_signals():
    context = _context(
        preference_attributes=[
            {
                "label": "preference_writing_style_concise",
                "value": "I prefer concise responses",
                "routing": "local_only",
                "status": "active",
            }
        ],
        artifact_chunks=[_chunk("notebook", "local only")],
    )
    assessment = evaluate_coverage(context, backend="external")

    assert assessment.counts.preferences == 0
    assert assessment.counts.artifacts == 0
    assert assessment.confidence == "insufficient_data"


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def test_guardrail_no_high_without_identity_support():
    # Artifacts can't carry confidence to high without attributes or preference attrs.
    # 3 sources: 4 + 4+4 + 3 + 3 = 18; cap 20 → 18. Below high (65) anyway.
    # Use many chunks to push toward but still check guardrail cap label.
    chunks = [
        {"title": f"doc_{i}", "content": "content " * 10, "chunk_index": 0, "routing": "local_only"}
        for i in range(3)
    ]
    assessment = evaluate_coverage(_context(artifact_chunks=chunks), backend="local")

    assert assessment.breakdown.cap_applied in {
        "no_identity_support",
        "artifact_only_single_source",
        None,
    }
    assert assessment.confidence != "high_confidence"


def test_guardrail_artifact_only_single_source_never_reaches_medium():
    # Single artifact source regardless of chunk count cannot exceed ARTIFACT_SCORE_CAP (20).
    # With artifact_grounded profile, medium starts at 40; 20 < 40 → low or insufficient.
    # Guardrail also ensures no_identity_support cap is applied if score were ever that high.
    chunks = [
        {"title": "single_doc", "content": "x" * 50, "chunk_index": i, "routing": "local_only"}
        for i in range(6)
    ]
    assessment = evaluate_coverage(_context(artifact_chunks=chunks), backend="local")

    assert assessment.confidence in {"insufficient_data", "low_confidence"}
    assert assessment.confidence != "medium_confidence"


def test_guardrail_does_not_fire_when_identity_present():
    # With strong attributes, guardrail 1 does not fire (cap_applied stays None).
    # 5 confirmed + high-conf attrs = 50 pts (cap) → medium_confidence.
    # Adding preferences or artifacts would push to high.
    attrs = [_confirmed_attr(confidence=0.90) for _ in range(5)]
    assessment = evaluate_coverage(_context(attributes=attrs), backend="local")

    assert assessment.breakdown.cap_applied is None
    assert assessment.confidence != "insufficient_data"


# ---------------------------------------------------------------------------
# Query-type profile thresholds
# ---------------------------------------------------------------------------

def test_narrow_preference_profile_has_lower_high_threshold():
    # A simple query with preference attributes → narrow_preference profile.
    # 2 confirmed pref attrs = 16; narrow_preference high=55.
    # 16 < 55 → not high. But with more prefs: PREF_SCORE_CAP=25 < 55. Always < high alone.
    # Test that profile is correctly detected and thresholds differ from default.
    pref = {"label": "preference_x", "value": "v", "routing": "local_only", "status": "confirmed"}
    context = _context(preference_attributes=[pref], retrieval_mode="simple")
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.breakdown.query_type_profile == "narrow_preference"


def test_broad_self_model_profile_detected_for_open_ended():
    attrs = [_active_attr()]
    context = _context(attributes=attrs, retrieval_mode="open_ended")
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.breakdown.query_type_profile == "broad_self_model"


def test_artifact_grounded_profile_detected_when_few_attrs_many_chunks():
    chunks = [
        {"title": "notes", "content": "content", "chunk_index": 0, "routing": "local_only"}
    ]
    # 0 or 1 attribute with chunks → artifact_grounded.
    context = _context(artifact_chunks=chunks, retrieval_mode="simple")
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.breakdown.query_type_profile == "artifact_grounded"


def test_narrow_preference_high_threshold_is_lower_than_default():
    # Verify the profile thresholds table is correctly wired.
    from engine.coverage_evaluator import _PROFILE_THRESHOLDS

    narrow_high = _PROFILE_THRESHOLDS["narrow_preference"][0]
    default_high = _PROFILE_THRESHOLDS["default"][0]

    assert narrow_high < default_high


def test_broad_self_model_high_threshold_is_higher_than_default():
    from engine.coverage_evaluator import _PROFILE_THRESHOLDS

    broad_high = _PROFILE_THRESHOLDS["broad_self_model"][0]
    default_high = _PROFILE_THRESHOLDS["default"][0]

    assert broad_high > default_high


# ---------------------------------------------------------------------------
# Consistency adjustment
# ---------------------------------------------------------------------------

def test_multi_signal_agreement_adds_bonus():
    # All three components contribute → +5 consistency bonus.
    attrs = [_confirmed_attr(confidence=0.90)]
    prefs = [{"label": "preference_x", "value": "v", "routing": "local_only", "status": "active"}]
    chunks = [_chunk("doc", "content here")]
    context = _context(attributes=attrs, preference_attributes=prefs, artifact_chunks=chunks)
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.breakdown.consistency_adjustment == 5.0


def test_no_consistency_bonus_when_only_one_component():
    context = _context(attributes=[_confirmed_attr()])
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.breakdown.consistency_adjustment == 0.0


# ---------------------------------------------------------------------------
# ScoreBreakdown is available
# ---------------------------------------------------------------------------

def test_score_breakdown_fields_are_present():
    context = _context(attributes=[_active_attr()])
    assessment = evaluate_coverage(context, backend="local")

    bd = assessment.breakdown
    assert isinstance(bd.attribute_score, float)
    assert isinstance(bd.preference_score, float)
    assert isinstance(bd.artifact_score, float)
    assert isinstance(bd.consistency_adjustment, float)
    assert isinstance(bd.total_score, float)
    assert bd.query_type_profile in {
        "default",
        "narrow_preference",
        "recommendation",
        "broad_self_model",
        "artifact_grounded",
    }
    assert bd.total_score == (
        bd.attribute_score
        + bd.preference_score
        + bd.artifact_score
        + bd.consistency_adjustment
    )


# ---------------------------------------------------------------------------
# Regression: insufficient_data short-circuit still works
# ---------------------------------------------------------------------------

def test_insufficient_data_notes_non_empty():
    assessment = evaluate_coverage(_context(), backend="local")

    assert assessment.confidence == "insufficient_data"
    assert assessment.notes is not None
    assert len(assessment.notes) > 0


def test_score_equals_breakdown_total():
    context = _context(attributes=[_active_attr()])
    assessment = evaluate_coverage(context, backend="local")

    assert assessment.score == assessment.breakdown.total_score
