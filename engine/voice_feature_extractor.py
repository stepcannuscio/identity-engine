"""Pure text-statistical voice feature extraction.

No model calls. Operates entirely on local text without any external I/O.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)
_CONTRACTION_RE = re.compile(
    r"\b("
    r"i'm|i've|i'd|i'll|"
    r"don't|doesn't|didn't|won't|wouldn't|can't|couldn't|shouldn't|"
    r"isn't|aren't|wasn't|weren't|haven't|hasn't|hadn't|"
    r"it's|that's|there's|they're|we're|you're|he's|she's|"
    r"i'm|let's|who's|what's|where's|when's|how's|"
    r"could've|would've|should've|might've|must've"
    r")\b",
    re.IGNORECASE,
)
_FIRST_PERSON_RE = re.compile(r"\b(i|i'm|i've|i'd|i'll|me|my|mine|myself)\b", re.IGNORECASE)
_QUESTION_RE = re.compile(r"\?")
_EM_DASH_RE = re.compile(r"—|--")
_ELLIPSIS_RE = re.compile(r"\.{3}|…")

_VOICE_MIN_WORDS = 50


@dataclass(frozen=True)
class VoiceFeatureProfile:
    """Structural statistics extracted from a block of user text."""

    avg_sentence_length: float
    question_frequency: float
    first_person_density: float
    contraction_rate: float
    em_dash_rate: float
    ellipsis_rate: float
    word_count: int


def extract(text: str) -> VoiceFeatureProfile | None:
    """Extract structural voice features from *text*.

    Returns ``None`` when the text is too short to produce a reliable profile
    (fewer than ``_VOICE_MIN_WORDS`` words).
    """
    words = _WORD_RE.findall(text)
    word_count = len(words)
    if word_count < _VOICE_MIN_WORDS:
        return None

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    sentence_count = max(len(sentences), 1)

    sentence_word_counts = [len(_WORD_RE.findall(s)) for s in sentences]
    avg_sentence_length = sum(sentence_word_counts) / sentence_count

    question_count = sum(1 for s in sentences if _QUESTION_RE.search(s))
    question_frequency = question_count / sentence_count

    first_person_count = len(_FIRST_PERSON_RE.findall(text))
    first_person_density = first_person_count / word_count

    contraction_count = len(_CONTRACTION_RE.findall(text))
    contraction_rate = contraction_count / word_count

    em_dash_count = len(_EM_DASH_RE.findall(text))
    em_dash_rate = em_dash_count / sentence_count

    ellipsis_count = len(_ELLIPSIS_RE.findall(text))
    ellipsis_rate = ellipsis_count / sentence_count

    return VoiceFeatureProfile(
        avg_sentence_length=round(avg_sentence_length, 2),
        question_frequency=round(question_frequency, 3),
        first_person_density=round(first_person_density, 3),
        contraction_rate=round(contraction_rate, 3),
        em_dash_rate=round(em_dash_rate, 3),
        ellipsis_rate=round(ellipsis_rate, 3),
        word_count=word_count,
    )


def insert_observation(conn, *, session_id: str, profile: VoiceFeatureProfile) -> str:
    """Persist a voice feature observation and refresh the running baseline."""
    observation_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO voice_feature_observations (
            id, session_id,
            avg_sentence_length, question_frequency, first_person_density,
            contraction_rate, em_dash_rate, ellipsis_rate, word_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            session_id,
            profile.avg_sentence_length,
            profile.question_frequency,
            profile.first_person_density,
            profile.contraction_rate,
            profile.em_dash_rate,
            profile.ellipsis_rate,
            profile.word_count,
        ),
    )
    _refresh_baseline(conn)
    return observation_id


def _refresh_baseline(conn) -> None:
    """Recompute the rolling aggregate baseline from all observations."""
    row = conn.execute(
        """
        SELECT
            COUNT(*),
            AVG(avg_sentence_length),
            AVG(question_frequency),
            AVG(first_person_density),
            AVG(contraction_rate),
            AVG(em_dash_rate),
            AVG(ellipsis_rate)
        FROM voice_feature_observations
        """
    ).fetchone()
    if row is None or row[0] == 0:
        return
    count = int(row[0])
    conn.execute(
        """
        INSERT INTO voice_baseline_profile (
            id, observation_count,
            avg_sentence_length, question_frequency, first_person_density,
            contraction_rate, em_dash_rate, ellipsis_rate
        )
        VALUES ('singleton', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            observation_count     = excluded.observation_count,
            avg_sentence_length   = excluded.avg_sentence_length,
            question_frequency    = excluded.question_frequency,
            first_person_density  = excluded.first_person_density,
            contraction_rate      = excluded.contraction_rate,
            em_dash_rate          = excluded.em_dash_rate,
            ellipsis_rate         = excluded.ellipsis_rate,
            updated_at            = CURRENT_TIMESTAMP
        """,
        (count, row[1], row[2], row[3], row[4], row[5], row[6]),
    )


@dataclass(frozen=True)
class VoiceBaselineProfile:
    """Aggregated baseline computed from accumulated voice feature observations."""

    observation_count: int
    avg_sentence_length: float
    question_frequency: float
    first_person_density: float
    contraction_rate: float
    em_dash_rate: float
    ellipsis_rate: float

    def to_guidance_lines(self) -> list[str]:
        """Return human-readable guidance lines derived from the baseline."""
        lines: list[str] = []
        lines.append(
            f"sentences averaging {self.avg_sentence_length:.0f} words"
        )
        if self.question_frequency >= 0.20:
            lines.append("questions appear frequently — keep an inquisitive tone")
        elif self.question_frequency <= 0.04:
            lines.append("questions are rare — prefer declarative phrasing")
        if self.contraction_rate >= 0.04:
            lines.append("contractions are common — keep the informal register")
        elif self.contraction_rate <= 0.01:
            lines.append("contractions are rare — preserve the formal register")
        if self.em_dash_rate >= 0.25:
            lines.append("em-dashes appear often — preserve them for rhythm")
        if self.ellipsis_rate >= 0.20:
            lines.append("ellipses appear often — preserve trailing pauses")
        return lines


_MIN_OBSERVATIONS_FOR_BASELINE = 5


def load_baseline(conn) -> VoiceBaselineProfile | None:
    """Load the computed baseline if enough observations exist."""
    row = conn.execute(
        """
        SELECT
            observation_count,
            avg_sentence_length,
            question_frequency,
            first_person_density,
            contraction_rate,
            em_dash_rate,
            ellipsis_rate
        FROM voice_baseline_profile
        WHERE id = 'singleton'
        """
    ).fetchone()
    if row is None or int(row[0]) < _MIN_OBSERVATIONS_FOR_BASELINE:
        return None
    return VoiceBaselineProfile(
        observation_count=int(row[0]),
        avg_sentence_length=float(row[1] or 0.0),
        question_frequency=float(row[2] or 0.0),
        first_person_density=float(row[3] or 0.0),
        contraction_rate=float(row[4] or 0.0),
        em_dash_rate=float(row[5] or 0.0),
        ellipsis_rate=float(row[6] or 0.0),
    )
