"""Attribute retrieval and scoring for identity query responses.

This module performs pure database and string operations (no model calls).
It scores active attributes against a user query, applies query-type budgets,
and returns the most relevant context for prompt grounding.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from engine.concept_expander import expand_query_tokens
from engine.embedding_index import compute_similarity_bonus
from engine.feedback_calibrator import load_retrieval_calibration
from engine.text_utils import contains_any_phrase, find_matching_phrases, tokenize

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
_RECENT_CONFIRM_DAYS = 180
_STALE_CONFIRM_DAYS = 540
_RECENT_UPDATE_DAYS = 120
_PHRASE_BOOST_CAP = 0.15


def _tokenize(text: str) -> set[str]:
    return tokenize(text, stopwords=STOPWORDS)


def _query_domains(query: str) -> set[str]:
    q = query.lower()
    matched: set[str] = set()
    for domain, triggers in DOMAIN_KEYWORDS.items():
        for trigger in triggers:
            if trigger in q:
                matched.add(domain)
                break
    return matched


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


def _recency_score(attribute: dict) -> float:
    now = datetime.now(UTC)
    last_confirmed = _parse_timestamp(attribute.get("last_confirmed"))
    updated_at = _parse_timestamp(attribute.get("updated_at"))

    if last_confirmed is not None:
        age_days = (now - last_confirmed).days
        if age_days <= _RECENT_CONFIRM_DAYS:
            return 0.10
        if age_days <= _STALE_CONFIRM_DAYS:
            return 0.04
        return -0.04

    if updated_at is not None:
        age_days = (now - updated_at).days
        if age_days <= _RECENT_UPDATE_DAYS:
            return 0.04
        if age_days > _STALE_CONFIRM_DAYS:
            return -0.06
    return 0.0


def _stability_penalty(attribute: dict) -> float:
    prior_versions = int(attribute.get("prior_versions", 0) or 0)
    if prior_versions <= 0:
        return 0.0
    if attribute.get("status") == "confirmed":
        return -min(prior_versions * 0.02, 0.05)
    return -min(prior_versions * 0.04, 0.12)


def _phrase_boost(query: str, attribute: dict) -> float:
    query_phrases = [
        phrase
        for phrase in (
            "sound like me",
            "my values",
            "my goals",
            "my voice",
            "my patterns",
            "deep work",
            "writing style",
            "next step",
        )
        if phrase in query.lower()
    ]
    if not query_phrases:
        return 0.0

    attribute_text = " ".join(
        str(attribute.get(key, "") or "")
        for key in ("label", "value", "elaboration")
    ).lower()
    matches = find_matching_phrases(attribute_text, query_phrases)
    return min(len(matches) * 0.05, _PHRASE_BOOST_CAP)


def score_attribute(
    query: str,
    attribute: dict,
    *,
    domain_hints: list[str] | None = None,
    intent_tags: list[str] | None = None,
    similarity_bonus: float = 0.0,
    calibration_delta: float = 0.0,
) -> float:
    """Score an attribute against the query using deterministic relevance heuristics."""
    query_tokens = _tokenize(query)
    label_tokens = _tokenize(str(attribute.get("label", "")))
    value_tokens = _tokenize(str(attribute.get("value", "")))
    elaboration_tokens = _tokenize(str(attribute.get("elaboration", "") or ""))

    if not query_tokens:
        lexical_score = 0.0
        expanded_score = 0.0
    else:
        denom = max(len(query_tokens), 1)
        lexical_label_overlap = min(1.0, len(query_tokens.intersection(label_tokens)) / denom)
        lexical_value_overlap = min(1.0, len(query_tokens.intersection(value_tokens)) / denom)
        lexical_elaboration_overlap = min(
            1.0,
            len(query_tokens.intersection(elaboration_tokens)) / denom,
        )
        lexical_score = (
            (lexical_label_overlap * 0.50)
            + (lexical_value_overlap * 0.35)
            + (lexical_elaboration_overlap * 0.15)
        )

        expanded_tokens = expand_query_tokens(
            query_tokens,
            domain_hints=domain_hints,
            query_text=query,
        )
        expanded_only_tokens = expanded_tokens.difference(query_tokens)
        if expanded_only_tokens:
            expanded_label_overlap = min(
                1.0,
                len(expanded_only_tokens.intersection(label_tokens)) / denom,
            )
            expanded_value_overlap = min(
                1.0,
                len(expanded_only_tokens.intersection(value_tokens)) / denom,
            )
            expanded_elaboration_overlap = min(
                1.0,
                len(expanded_only_tokens.intersection(elaboration_tokens)) / denom,
            )
            expanded_score = (
                (expanded_label_overlap * 0.50)
                + (expanded_value_overlap * 0.35)
                + (expanded_elaboration_overlap * 0.15)
            )
        else:
            expanded_score = 0.0

    matched_domains = set(domain_hints or []) or _query_domains(query)
    domain_score = 0.0
    if matched_domains and attribute.get("domain") in matched_domains:
        domain_score += 0.40
    if "planning" in (intent_tags or []) and attribute.get("domain") in {"goals", "patterns"}:
        domain_score += 0.10
    if "voice_adaptation" in (intent_tags or []) and attribute.get("domain") == "voice":
        domain_score += 0.10

    confidence = float(attribute.get("confidence", 0.0) or 0.0)
    status = str(attribute.get("status", ""))
    source = str(attribute.get("source", ""))
    trust_score = 0.0
    if status == "confirmed":
        trust_score += 0.18
    elif status == "active":
        trust_score += 0.08
    if source in {"explicit", "reflection"}:
        trust_score += 0.04
    elif source == "inferred":
        trust_score -= 0.05

    return round(
        max(
            0.0,
            (lexical_score * 0.32)
            + (expanded_score * 0.20)
            + (domain_score * 0.30)
            + (confidence * 0.16)
            + min(max(similarity_bonus, 0.0), 0.10)
            + max(min(calibration_delta, 0.15), -0.15)
            + trust_score
            + _recency_score(attribute)
            + _phrase_boost(query, attribute)
            + _stability_penalty(attribute),
        ),
        4,
    )


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


def retrieve_attribute_candidates(
    query: str,
    query_type: str,
    conn,
    *,
    domain_hints: list[str] | None = None,
    intent_tags: list[str] | None = None,
    source_profile: str | None = None,
    provider_config=None,
) -> list[dict]:
    """Return scored identity-attribute candidates before final prompt blending."""
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
            a.status,
            a.source,
            a.updated_at,
            a.last_confirmed,
            (
                SELECT COUNT(*)
                FROM attributes history
                WHERE history.domain_id = a.domain_id
                  AND history.label = a.label
                  AND history.status IN ('superseded', 'rejected', 'retracted')
            ) AS prior_versions
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
            "source": row[8],
            "updated_at": row[9],
            "last_confirmed": row[10],
            "prior_versions": int(row[11] or 0),
        }
        scored.append(attr)

    similarity_bonus = compute_similarity_bonus(
        conn,
        query,
        scored,
        provider_config=provider_config,
    )
    calibration = load_retrieval_calibration(
        conn,
        source_profile=source_profile,
    )
    for attr in scored:
        attr["score"] = score_attribute(
            query,
            attr,
            domain_hints=domain_hints,
            intent_tags=intent_tags,
            similarity_bonus=similarity_bonus.get(str(attr["id"]), 0.0),
            calibration_delta=calibration.get(str(attr["domain"]), 0.0),
        )

    scored.sort(key=lambda x: x["score"], reverse=True)

    budget = budget_for_query_type(query_type)
    threshold = float(budget["score_threshold"])
    filtered = [a for a in scored if a["score"] >= threshold]

    filtered = _apply_domain_intent_fallback(query, scored, filtered)

    if query_type == "open_ended":
        filtered = _apply_open_ended_domain_expansion(scored, filtered)
        filtered.sort(key=lambda x: x["score"], reverse=True)

    return filtered


def retrieve_attributes(
    query: str,
    query_type: str,
    conn,
    *,
    source_profile: str | None = None,
    provider_config=None,
) -> list[dict]:
    """Retrieve and score active attributes for a query, then apply query budget rules."""
    filtered = retrieve_attribute_candidates(
        query,
        query_type,
        conn,
        source_profile=source_profile,
        provider_config=provider_config,
    )
    budget = budget_for_query_type(query_type)

    constrained = _apply_domain_cap(
        filtered,
        max_domains=int(budget["max_domains"]),
        max_attributes=int(budget["max_attributes"]),
    )

    return constrained[: int(budget["max_attributes"])]
