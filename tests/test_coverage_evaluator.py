"""Tests for engine/coverage_evaluator.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.context_assembler import AssembledContext
from engine.coverage_evaluator import (
    ATTRIBUTE_CAP,
    ATTRIBUTE_WEIGHT,
    HIGH_SCORE_THRESHOLD,
    MEDIUM_SCORE_THRESHOLD,
    evaluate_coverage,
)


def _context(
    attributes: list[dict] | None = None,
    preference_attributes: list[dict] | None = None,
    artifact_chunks: list[dict] | None = None,
) -> AssembledContext:
    attributes = attributes or []
    preference_attributes = preference_attributes or []
    artifact_chunks = artifact_chunks or []
    return AssembledContext(
        task_type="query",
        input_text="test",
        attributes=attributes,
        session_history=[],
        domains_used=[],
        attribute_count=len(attributes),
        retrieval_mode="simple",
        was_trimmed=False,
        contains_local_only=False,
        preference_attributes=preference_attributes,
        preference_count=len(preference_attributes),
        artifact_chunks=artifact_chunks,
        artifact_count=len(artifact_chunks),
    )


def test_evaluate_coverage_empty_context_is_insufficient_data():
    assessment = evaluate_coverage(_context(), backend="local")

    assert assessment.confidence == "insufficient_data"
    assert assessment.counts.attributes == 0
    assert assessment.counts.preferences == 0
    assert assessment.counts.artifacts == 0
    assert assessment.score == 0.0
    assert assessment.notes is not None


def test_evaluate_coverage_single_active_attribute_reaches_medium():
    context = _context(
        attributes=[
            {
                "domain": "goals",
                "label": "priority",
                "value": "Ship project",
                "status": "active",
                "confidence": 0.6,
                "routing": "external_ok",
            }
        ]
    )

    assessment = evaluate_coverage(context, backend="local")

    assert assessment.confidence == "medium_confidence"
    assert assessment.score == ATTRIBUTE_WEIGHT
    assert assessment.score >= MEDIUM_SCORE_THRESHOLD


def test_evaluate_coverage_weak_artifact_only_stays_low_not_insufficient():
    context = _context(
        artifact_chunks=[
            {
                "title": "notes",
                "content": "...",
                "chunk_index": 0,
                "routing": "local_only",
            }
        ]
    )

    assessment = evaluate_coverage(context, backend="local")

    assert assessment.confidence == "low_confidence"
    assert assessment.score > 0


def test_evaluate_coverage_rich_context_reaches_high():
    context = _context(
        attributes=[
            {
                "domain": "goals",
                "label": "priority",
                "value": "Ship project",
                "status": "confirmed",
                "confidence": 0.95,
                "routing": "external_ok",
            },
            {
                "domain": "patterns",
                "label": "morning_focus",
                "value": "Deep work in morning",
                "status": "active",
                "confidence": 0.8,
                "routing": "local_only",
            },
        ],
        preference_attributes=[
            {
                "label": "preference_writing_style_concise",
                "value": "I prefer concise responses",
                "routing": "local_only",
            },
            {
                "label": "preference_format_brief",
                "value": "I prefer brief replies",
                "routing": "local_only",
            },
        ],
        artifact_chunks=[
            {
                "title": "notebook",
                "content": "",
                "chunk_index": 0,
                "routing": "local_only",
            }
        ],
    )

    assessment = evaluate_coverage(context, backend="local")

    assert assessment.confidence == "high_confidence"
    assert assessment.score >= HIGH_SCORE_THRESHOLD


def test_evaluate_coverage_external_backend_drops_local_only_signals():
    context = _context(
        preference_attributes=[
            {
                "label": "preference_writing_style_concise",
                "value": "I prefer concise responses",
                "routing": "local_only",
            }
        ],
        artifact_chunks=[
            {
                "title": "notebook",
                "content": "local only",
                "chunk_index": 0,
                "routing": "local_only",
            }
        ],
    )

    assessment = evaluate_coverage(context, backend="external")

    assert assessment.counts.preferences == 0
    assert assessment.counts.artifacts == 0
    assert assessment.confidence == "insufficient_data"


def test_evaluate_coverage_applies_attribute_cap():
    many_attributes = [
        {
            "domain": "goals",
            "label": f"goal_{i}",
            "value": "...",
            "status": "active",
            "confidence": 0.5,
            "routing": "external_ok",
        }
        for i in range(ATTRIBUTE_CAP + 3)
    ]
    capped = _context(attributes=many_attributes[:ATTRIBUTE_CAP])
    overflow = _context(attributes=many_attributes)

    assert evaluate_coverage(capped, backend="local").score == evaluate_coverage(
        overflow, backend="local"
    ).score


def test_evaluate_coverage_confirmed_attribute_adds_bonus():
    active_context = _context(
        attributes=[
            {
                "domain": "goals",
                "label": "priority",
                "value": "Ship",
                "status": "active",
                "confidence": 0.5,
                "routing": "external_ok",
            }
        ]
    )
    confirmed_context = _context(
        attributes=[
            {
                **active_context.attributes[0],
                "status": "confirmed",
            }
        ]
    )

    assert (
        evaluate_coverage(confirmed_context, backend="local").score
        > evaluate_coverage(active_context, backend="local").score
    )
