"""Attribute retrieval and scoring for identity query responses.

This module performs pure database and string operations (no model calls).
It scores active attributes against a user query, applies query-type budgets,
and returns the most relevant context for prompt grounding.
"""

from __future__ import annotations

import re
from collections import defaultdict

SIMPLE_BUDGET = {
    "max_attributes": 8,
    "max_domains": 2,
    "score_threshold": 0.3,
}

OPEN_ENDED_BUDGET = {
    "max_attributes": 20,
    "max_domains": 8,
    "score_threshold": 0.15,
}

DOMAIN_KEYWORDS = {
    "goals": [
        "goal", "want", "achieve", "future", "plan", "aspire",
        "working toward", "trying to", "next",
    ],
    "personality": [
        "style", "approach", "tend", "typically", "usually",
        "respond", "handle", "work", "behave",
    ],
    "fears": [
        "afraid", "worry", "concern", "scared", "anxious", "risk",
        "failure", "avoid",
    ],
    "values": [
        "believe", "important", "matter", "value", "principle",
        "integrity", "care about", "non-negotiable",
    ],
    "patterns": [
        "habit", "pattern", "always", "often", "tend to", "procrastin",
        "productive", "morning", "stress", "distract",
    ],
    "voice": [
        "write", "sound", "tone", "communicate", "style", "words",
        "express",
    ],
    "relationships": [
        "friend", "trust", "people", "close", "relationship", "care",
        "connect", "pull back",
    ],
    "beliefs": [
        "think", "believe", "opinion", "view", "perspective",
        "convinced", "feel like",
    ],
}

STOPWORDS = {
    "a", "an", "the", "is", "are", "do", "i", "my", "what", "how",
    "can", "should", "would", "will", "me", "you", "it", "this", "that",
    "and", "or", "but", "in", "on", "at", "to", "of", "for",
}

TOKEN_RE = re.compile(r"[a-z0-9']+")


def _tokenize(text: str) -> set[str]:
    tokens = {t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS}
    return tokens


def _query_domains(query: str) -> set[str]:
    q = query.lower()
    matched: set[str] = set()
    for domain, triggers in DOMAIN_KEYWORDS.items():
        for trigger in triggers:
            if trigger in q:
                matched.add(domain)
                break
    return matched


def score_attribute(query: str, attribute: dict) -> float:
    """Score an attribute against the query using deterministic relevance heuristics."""
    query_tokens = _tokenize(query)
    attr_text = f"{attribute.get('label', '')} {attribute.get('value', '')}"
    attr_tokens = _tokenize(attr_text)

    if not query_tokens or not attr_tokens:
        keyword_score = 0.0
    else:
        overlap = len(query_tokens.intersection(attr_tokens))
        keyword_score = overlap / max(len(query_tokens), len(attr_tokens))

    matched_domains = _query_domains(query)
    if matched_domains and attribute.get("domain") in matched_domains:
        domain_score = 0.3
    else:
        domain_score = 0.0

    confidence = float(attribute.get("confidence", 0.0) or 0.0)
    confirmed_bonus = 0.1 if attribute.get("status") == "confirmed" else 0.0

    return (keyword_score * 0.5) + (domain_score * 0.3) + (confidence * 0.2) + confirmed_bonus


def budget_for_query_type(query_type: str) -> dict:
    return SIMPLE_BUDGET if query_type == "simple" else OPEN_ENDED_BUDGET


def _apply_open_ended_domain_expansion(scored: list[dict], selected: list[dict]) -> list[dict]:
    if not scored or not selected:
        return selected

    top_domain = scored[0]["domain"]
    in_selected = [a for a in selected if a["domain"] == top_domain]
    if len(in_selected) >= 2:
        return selected

    for candidate in scored:
        if candidate["domain"] == top_domain and candidate not in selected:
            return selected + [candidate]

    return selected


def _apply_domain_intent_fallback(
    query: str,
    scored: list[dict],
    selected: list[dict],
) -> list[dict]:
    """Ensure explicit domain-intent queries include at least one attr per matched domain.

    When a query clearly references one or more identity domains (for example:
    "What are my current goals?"), lexical overlap may still be weak enough for
    threshold filtering to drop every attribute. This fallback injects the
    top-scoring attribute for each explicitly matched domain if missing.
    """
    matched_domains = _query_domains(query)
    if not matched_domains:
        return selected

    selected_by_domain = {a["domain"] for a in selected}
    injected: list[dict] = []

    for domain in matched_domains:
        if domain in selected_by_domain:
            continue
        for candidate in scored:
            if candidate["domain"] == domain:
                injected.append(candidate)
                break

    if not injected:
        return selected

    # Keep intent-injected attributes first so downstream domain-cap logic
    # preserves user-requested domains.
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for row in injected + selected:
        row_id = str(row.get("id", ""))
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        merged.append(row)
    return merged


def _apply_domain_cap(results: list[dict], max_domains: int, max_attributes: int) -> list[dict]:
    if not results:
        return []

    domains_in_order: list[str] = []
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        domain = row["domain"]
        by_domain[domain].append(row)
        if domain not in domains_in_order:
            domains_in_order.append(domain)

    if len(domains_in_order) <= max_domains:
        return results[:max_attributes]

    allowed_domains = set(domains_in_order[:max_domains])
    selected: list[dict] = []

    # Keep top-scoring attribute from each allowed domain first.
    for domain in domains_in_order:
        if domain in allowed_domains and by_domain[domain]:
            selected.append(by_domain[domain][0])

    if len(selected) >= max_attributes:
        return selected[:max_attributes]

    # Fill remaining slots with next highest from allowed domains.
    for row in results:
        if row in selected:
            continue
        if row["domain"] in allowed_domains:
            selected.append(row)
            if len(selected) >= max_attributes:
                break

    return selected


def retrieve_attributes(query: str, query_type: str, conn) -> list[dict]:
    """Retrieve and score active attributes for a query, then apply query budget rules."""
    rows = conn.execute(
        """
        SELECT
            a.id,
            d.name,
            a.label,
            a.value,
            a.elaboration,
            a.confidence,
            a.routing,
            a.status
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE a.status IN ('active', 'confirmed')
        """
    ).fetchall()

    scored: list[dict] = []
    for row in rows:
        attr = {
            "id": row[0],
            "domain": row[1],
            "label": row[2],
            "value": row[3],
            "elaboration": row[4],
            "confidence": float(row[5]),
            "routing": row[6],
            "status": row[7],
        }
        attr["score"] = score_attribute(query, attr)
        scored.append(attr)

    scored.sort(key=lambda x: x["score"], reverse=True)

    budget = budget_for_query_type(query_type)
    threshold = float(budget["score_threshold"])
    filtered = [a for a in scored if a["score"] >= threshold]

    filtered = _apply_domain_intent_fallback(query, scored, filtered)

    if query_type == "open_ended":
        filtered = _apply_open_ended_domain_expansion(scored, filtered)
        filtered.sort(key=lambda x: x["score"], reverse=True)

    constrained = _apply_domain_cap(
        filtered,
        max_domains=int(budget["max_domains"]),
        max_attributes=int(budget["max_attributes"]),
    )

    return constrained[: int(budget["max_attributes"])]
