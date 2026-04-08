"""Deterministic query classification for retrieval budget routing.

This module performs pure string analysis (no model calls) to classify user
queries as either "simple" or "open_ended".
"""

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


_WORD_RE = re.compile(r"[a-zA-Z0-9']+")


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
