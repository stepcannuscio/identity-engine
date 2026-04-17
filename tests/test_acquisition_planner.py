"""Tests for deterministic acquisition planning."""

from __future__ import annotations

from engine.acquisition_planner import build_acquisition_plan
from engine.context_assembler import AssembledContext
from engine.coverage_evaluator import (
    ConfidenceLabel,
    CoverageAssessment,
    CoverageCounts,
    ScoreBreakdown,
)
from engine.preference_summary import PreferenceSummaryPayload, empty_preference_summary


def _context(
    *,
    input_text: str = "What are my current goals?",
    source_profile: str = "self_question",
    attributes: list[dict] | None = None,
    preference_attributes: list[dict] | None = None,
    preference_summary: PreferenceSummaryPayload | None = None,
    artifact_chunks: list[dict] | None = None,
) -> AssembledContext:
    return AssembledContext(
        task_type="query",
        input_text=input_text,
        attributes=attributes or [],
        session_history=[],
        domains_used=[],
        attribute_count=len(attributes or []),
        retrieval_mode="simple",
        source_profile=source_profile,
        preference_attributes=preference_attributes or [],
        preference_summary=preference_summary or empty_preference_summary(),
        preference_count=len(preference_attributes or []),
        artifact_chunks=artifact_chunks or [],
        artifact_count=len(artifact_chunks or []),
        artifact_sources=[],
    )


def _coverage(confidence: ConfidenceLabel) -> CoverageAssessment:
    return CoverageAssessment(
        counts=CoverageCounts(attributes=0, preferences=0, artifacts=0),
        score=0.0,
        confidence=confidence,
        notes=None,
        breakdown=ScoreBreakdown(
            attribute_score=0.0,
            preference_score=0.0,
            artifact_score=0.0,
            consistency_adjustment=0.0,
            total_score=0.0,
            cap_applied=None,
            query_type_profile="general",
        ),
    )


def test_self_question_with_empty_domain_adds_identity_gap_and_interview():
    plan = build_acquisition_plan(
        "What are my current goals?",
        _context(),
        _coverage("insufficient_data"),
    )

    assert plan.status == "suggested"
    assert plan.gaps[0].kind == "identity"
    assert plan.gaps[0].domain == "goals"
    assert plan.suggestions[0].kind == "quick_capture"
    assert plan.suggestions[1].kind == "interview_question"
    assert plan.suggestions[1].action["domain"] == "goals"


def test_strong_identity_support_suppresses_self_question_gap():
    plan = build_acquisition_plan(
        "What are my current goals?",
        _context(
            attributes=[
                {
                    "domain": "goals",
                    "label": "priority",
                    "value": "Ship the backend.",
                    "status": "confirmed",
                    "confidence": 0.9,
                }
            ]
        ),
        _coverage("low_confidence"),
    )

    assert plan.status == "not_needed"
    assert plan.gaps == []


def test_preference_sensitive_query_suggests_preference_capture():
    plan = build_acquisition_plan(
        "What should I cook this week?",
        _context(
            input_text="What should I cook this week?",
            source_profile="preference_sensitive",
        ),
        _coverage("insufficient_data"),
    )

    assert plan.status == "suggested"
    assert plan.gaps[0].kind == "preference"
    assert plan.suggestions[0].kind == "quick_capture"
    assert plan.suggestions[0].action["target"] == "preference_signal"


def test_evidence_based_query_suggests_artifact_upload():
    plan = build_acquisition_plan(
        "What do my notes say about burnout?",
        _context(
            input_text="What do my notes say about burnout?",
            source_profile="evidence_based",
        ),
        _coverage("insufficient_data"),
    )

    assert plan.status == "suggested"
    assert plan.gaps[0].kind == "artifact"
    assert plan.suggestions[0].kind == "artifact_upload"


def test_medium_confidence_only_suggests_when_required_source_is_missing():
    no_gap_plan = build_acquisition_plan(
        "What should I cook this week?",
        _context(
            input_text="What should I cook this week?",
            source_profile="preference_sensitive",
            preference_attributes=[{"label": "preference_meals", "value": "Prefer simple meals"}],
        ),
        _coverage("medium_confidence"),
    )
    gap_plan = build_acquisition_plan(
        "What should I cook this week?",
        _context(
            input_text="What should I cook this week?",
            source_profile="preference_sensitive",
        ),
        _coverage("medium_confidence"),
    )

    assert no_gap_plan.status == "not_needed"
    assert gap_plan.status == "suggested"


def test_suggestion_count_is_capped_at_three():
    plan = build_acquisition_plan(
        "What are my goals, values, and fears?",
        _context(
            input_text="What are my goals, values, and fears?",
            source_profile="self_question",
        ),
        _coverage("insufficient_data"),
    )

    assert plan.status == "suggested"
    assert len(plan.suggestions) == 3
