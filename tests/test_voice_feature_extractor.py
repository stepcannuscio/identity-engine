"""Tests for Phase 6 voice feature extraction and baseline profile."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.voice_feature_extractor import (
    VoiceBaselineProfile,
    VoiceFeatureProfile,
    extract,
    insert_observation,
    load_baseline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHORT_TEXT = "I am a short text."

_LONG_INFORMAL = (
    "I've been thinking a lot about this lately — I don't always know what I want, "
    "but I think that's okay. I'm pretty comfortable with uncertainty. "
    "Honestly, I don't mind if things change — I'd rather adapt than cling to a plan. "
    "I'm also someone who prefers working alone in the mornings. "
    "The afternoons are fine but I can't focus the same way. "
    "I tend to write in short bursts... then pause and revisit. "
    "Does that make sense? I find it hard to explain sometimes."
)

_LONG_FORMAL = (
    "The individual in question exhibits a consistent preference for structured "
    "environments that afford predictability and methodological rigor. "
    "Observations suggest that performance is maximized under conditions of low "
    "ambient distraction, particularly during morning hours. "
    "The subject articulates a clear aversion to prolonged ambiguity, preferring "
    "explicit goal delineation prior to commencing complex projects. "
    "Furthermore, the individual demonstrates measurable sensitivity to interpersonal "
    "conflict and tends to disengage from environments characterized by persistent tension. "
    "The subject communicates in extended, clause-rich sentences with precise vocabulary."
)


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


# ---------------------------------------------------------------------------
# extract() tests
# ---------------------------------------------------------------------------


def test_extract_returns_none_for_short_text():
    assert extract(_SHORT_TEXT) is None


def test_extract_returns_profile_for_long_text():
    profile = extract(_LONG_INFORMAL)
    assert isinstance(profile, VoiceFeatureProfile)
    assert profile.word_count >= 50


def test_extract_word_count_matches():
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    assert profile.word_count > 0


def test_extract_avg_sentence_length_positive():
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    assert profile.avg_sentence_length > 0


def test_extract_contraction_rate_higher_for_informal():
    informal = extract(_LONG_INFORMAL)
    formal = extract(_LONG_FORMAL)
    assert informal is not None
    assert formal is not None
    assert informal.contraction_rate > formal.contraction_rate


def test_extract_first_person_density_high_for_first_person_text():
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    assert profile.first_person_density > 0.05


def test_extract_ellipsis_rate_detected():
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    assert profile.ellipsis_rate > 0


def test_extract_em_dash_rate_detected():
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    assert profile.em_dash_rate > 0


def test_extract_question_frequency_detected():
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    assert profile.question_frequency > 0


# ---------------------------------------------------------------------------
# insert_observation / load_baseline tests
# ---------------------------------------------------------------------------


def test_load_baseline_returns_none_when_no_observations(conn):
    assert load_baseline(conn) is None


def test_load_baseline_returns_none_below_threshold(conn):
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    for _ in range(4):
        insert_observation(conn, session_id="sess-1", profile=profile)
        conn.commit()
    assert load_baseline(conn) is None


def test_load_baseline_returns_profile_at_threshold(conn):
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    for i in range(5):
        insert_observation(conn, session_id=f"sess-{i}", profile=profile)
        conn.commit()
    baseline = load_baseline(conn)
    assert isinstance(baseline, VoiceBaselineProfile)
    assert baseline.observation_count == 5


def test_baseline_avg_sentence_length_reasonable(conn):
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    for i in range(5):
        insert_observation(conn, session_id=f"sess-{i}", profile=profile)
        conn.commit()
    baseline = load_baseline(conn)
    assert baseline is not None
    assert baseline.avg_sentence_length > 0


def test_baseline_idempotent_on_repeated_refresh(conn):
    profile = extract(_LONG_INFORMAL)
    assert profile is not None
    for i in range(5):
        insert_observation(conn, session_id=f"sess-{i}", profile=profile)
        conn.commit()
    b1 = load_baseline(conn)
    insert_observation(conn, session_id="sess-extra", profile=profile)
    conn.commit()
    b2 = load_baseline(conn)
    assert b2 is not None
    assert b1 is not None
    assert b2.observation_count == b1.observation_count + 1


# ---------------------------------------------------------------------------
# VoiceBaselineProfile.to_guidance_lines() tests
# ---------------------------------------------------------------------------


def test_guidance_lines_include_sentence_length():
    baseline = VoiceBaselineProfile(
        observation_count=10,
        avg_sentence_length=12.5,
        question_frequency=0.10,
        first_person_density=0.08,
        contraction_rate=0.05,
        em_dash_rate=0.10,
        ellipsis_rate=0.10,
    )
    lines = baseline.to_guidance_lines()
    assert any("sentences averaging" in line for line in lines)


def test_guidance_lines_flag_contractions_when_common():
    baseline = VoiceBaselineProfile(
        observation_count=10,
        avg_sentence_length=12.0,
        question_frequency=0.05,
        first_person_density=0.05,
        contraction_rate=0.06,
        em_dash_rate=0.0,
        ellipsis_rate=0.0,
    )
    lines = baseline.to_guidance_lines()
    assert any("contractions" in line for line in lines)


def test_guidance_lines_flag_questions_when_frequent():
    baseline = VoiceBaselineProfile(
        observation_count=10,
        avg_sentence_length=10.0,
        question_frequency=0.30,
        first_person_density=0.05,
        contraction_rate=0.02,
        em_dash_rate=0.0,
        ellipsis_rate=0.0,
    )
    lines = baseline.to_guidance_lines()
    assert any("questions appear frequently" in line for line in lines)


def test_guidance_lines_flag_em_dash_when_common():
    baseline = VoiceBaselineProfile(
        observation_count=10,
        avg_sentence_length=10.0,
        question_frequency=0.05,
        first_person_density=0.05,
        contraction_rate=0.02,
        em_dash_rate=0.30,
        ellipsis_rate=0.0,
    )
    lines = baseline.to_guidance_lines()
    assert any("em-dash" in line for line in lines)


def test_guidance_lines_flag_formal_when_no_contractions():
    baseline = VoiceBaselineProfile(
        observation_count=10,
        avg_sentence_length=20.0,
        question_frequency=0.02,
        first_person_density=0.01,
        contraction_rate=0.005,
        em_dash_rate=0.0,
        ellipsis_rate=0.0,
    )
    lines = baseline.to_guidance_lines()
    assert any("formal" in line for line in lines)
