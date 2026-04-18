"""Shared deterministic text helpers for query understanding and retrieval."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def normalize_token(token: str) -> str:
    """Return a lightly normalized token for deterministic matching."""
    normalized = token.lower()
    if len(normalized) > 5 and normalized.endswith("ing"):
        normalized = normalized[:-3]
    elif len(normalized) > 4 and normalized.endswith("ed"):
        normalized = normalized[:-2]
    if len(normalized) > 4 and normalized.endswith("ies"):
        normalized = normalized[:-3] + "y"
    elif len(normalized) > 4 and normalized.endswith("s"):
        normalized = normalized[:-1]
    return normalized


def tokenize(text: str, *, stopwords: set[str] | frozenset[str] | None = None) -> set[str]:
    """Tokenize and normalize one text blob."""
    blocked = stopwords or set()
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text.lower()):
        token = normalize_token(raw)
        if token and token not in blocked:
            tokens.add(token)
    return tokens


def find_matching_phrases(text: str, phrases: tuple[str, ...] | list[str]) -> list[str]:
    """Return phrases that occur in the normalized text."""
    lowered = f" {text.lower()} "
    return [phrase for phrase in phrases if f" {phrase.lower()} " in lowered]


def contains_any_phrase(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    """Return True when any phrase occurs in the text."""
    return bool(find_matching_phrases(text, phrases))

