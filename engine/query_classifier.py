"""Deterministic query classification for retrieval and source routing.

This module performs pure string analysis (no model calls) to classify user
queries along two axes:

- retrieval budget: ``simple`` or ``open_ended``
- source profile: ``self_question``, ``evidence_based``,
  ``preference_sensitive``, or ``general``
"""

from dataclasses import dataclass
import re

DIRECT_DOMAIN_KEYWORDS = {
    "goals",
    "values",
    "personality",
    "patterns",
    "voice",
    "relationships",
    "fears",
    "beliefs",
}

DIRECT_PHRASES = (
    "what is",
    "what are",
    "do i",
    "am i",
    "list my",
    "show me",
)

SUBORDINATE_MARKERS = (
    "because",
    "although",
    "though",
    "while",
    "whereas",
    "since",
    "unless",
    "if ",
    "when ",
    "which",
    "that ",
)

SELF_QUESTION_PATTERNS = (
    "who am i",
    "what am i like",
    "how do i tend to",
    "how do i usually",
    "what are my",
    "what is my",
    "am i",
    "do i",
)

EVIDENCE_BASED_TERMS = (
    "artifact",
    "artifacts",
    "document",
    "documents",
    "evidence",
    "file",
    "files",
    "journal",
    "journals",
    "note",
    "notes",
    "notebook",
    "notebooks",
    "prior material",
    "prior materials",
    "upload",
    "uploaded",
    "writing sample",
    "writing samples",
)

PREFERENCE_SENSITIVE_TERMS = (
    "choose",
    "choices",
    "draft",
    "plan",
    "planning",
    "pick",
    "recommend",
    "recommendation",
    "rewrite",
    "select",
    "selection",
    "suggest",
)

_WORD_RE = re.compile(r"[a-zA-Z0-9']+")


@dataclass(frozen=True)
class QueryPlan:
    """Deterministic query routing result."""

    retrieval_mode: str
    source_profile: str


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _contains_domain_keyword(text: str) -> bool:
    words = set(_WORD_RE.findall(text.lower()))
    return bool(words.intersection(DIRECT_DOMAIN_KEYWORDS))


def _has_simple_question_shape(query: str) -> bool:
    q = query.strip().lower()
    if not q.endswith("?"):
        return False
    if q.count("?") != 1:
        return False
    if "," in q or ";" in q or ":" in q:
        return False
    return not any(marker in q for marker in SUBORDINATE_MARKERS)


def classify_query(query: str) -> str:
    """Classify a user query as "simple" or "open_ended".

    Rules (any match => "simple"):
    - Under 8 words
    - Contains direct identity domain keyword
    - Contains direct request phrase like "what is" / "show me"
    - Ends with a single question mark and has no subordinate clauses
    """
    normalized = query.strip().lower()

    if _word_count(normalized) < 8:
        return "simple"

    if _contains_domain_keyword(normalized):
        return "simple"

    if any(phrase in normalized for phrase in DIRECT_PHRASES):
        return "simple"

    if _has_simple_question_shape(query):
        return "simple"

    return "open_ended"


def classify_source_profile(query: str) -> str:
    """Return the internal source-mix profile for one query."""
    normalized = query.strip().lower()
    words = set(_WORD_RE.findall(normalized))

    if any(term in normalized for term in EVIDENCE_BASED_TERMS):
        return "evidence_based"

    if any(term in normalized for term in PREFERENCE_SENSITIVE_TERMS):
        return "preference_sensitive"

    if any(pattern in normalized for pattern in SELF_QUESTION_PATTERNS):
        return "self_question"

    if words.intersection(DIRECT_DOMAIN_KEYWORDS):
        return "self_question"

    return "general"


def build_query_plan(query: str) -> QueryPlan:
    """Return both the public retrieval mode and the internal source profile."""
    return QueryPlan(
        retrieval_mode=classify_query(query),
        source_profile=classify_source_profile(query),
    )
