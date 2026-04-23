"""Deterministic domain-aware concept expansion for retrieval."""

from __future__ import annotations

from dataclasses import dataclass

from engine.text_utils import find_matching_phrases, normalize_token, tokenize


@dataclass(frozen=True)
class ConceptCluster:
    """One deterministic concept cluster used for query-side expansion."""

    key: str
    domains: frozenset[str]
    phrases: frozenset[str]
    synonyms: frozenset[str]
    label_aliases: frozenset[str]


def _normalize_values(values: tuple[str, ...] | list[str]) -> frozenset[str]:
    return frozenset(normalize_token(value) for value in values if value.strip())


def _phrase_values(values: tuple[str, ...] | list[str]) -> frozenset[str]:
    return frozenset(" ".join(value.lower().split()) for value in values if value.strip())


_CLUSTERS: tuple[ConceptCluster, ...] = (
    ConceptCluster(
        key="goals_drive",
        domains=frozenset({"goals", "values"}),
        phrases=_phrase_values(["what drives me", "what motivates me", "what energizes me"]),
        synonyms=_normalize_values(
            [
                "drive",
                "drives",
                "motivates",
                "motivation",
                "purpose",
                "fuel",
                "energize",
                "ambition",
                "aspiration",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "intrinsic_drive",
                "intrinsic_motivation",
                "motivation_style",
                "purpose",
                "core_goal",
            ]
        ),
    ),
    ConceptCluster(
        key="goals_direction",
        domains=frozenset({"goals"}),
        phrases=_phrase_values(["what am i working toward", "where am i headed"]),
        synonyms=_normalize_values(
            [
                "goal",
                "goals",
                "direction",
                "vision",
                "aim",
                "future",
                "pursuit",
                "plan",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "career_direction",
                "goal_horizon",
                "future_direction",
                "north_star",
                "priority_goal",
            ]
        ),
    ),
    ConceptCluster(
        key="social_energy",
        domains=frozenset({"personality", "patterns", "relationships", "fears"}),
        phrases=_phrase_values(
            [
                "how i recharge",
                "being around people",
                "social battery",
                "drained by meetings",
            ]
        ),
        synonyms=_normalize_values(
            [
                "introvert",
                "introverted",
                "introversion",
                "extrovert",
                "extroverted",
                "extroversion",
                "solitude",
                "alone",
                "quiet",
                "reserved",
                "crowds",
                "groups",
                "meeting",
                "meetings",
                "social",
                "recharge",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "social_orientation",
                "social_energy",
                "solitude_need",
                "meeting_energy",
                "crowd_tolerance",
                "people_energy",
                "connection_needs",
                "one_on_one_preference",
            ]
        ),
    ),
    ConceptCluster(
        key="personality_core",
        domains=frozenset({"personality"}),
        phrases=_phrase_values(["who am i", "what am i like", "my personality"]),
        synonyms=_normalize_values(
            [
                "identity",
                "personality",
                "nature",
                "temperament",
                "disposition",
                "character",
                "core",
                "authentic",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "core_identity",
                "personality_style",
                "social_orientation",
                "temperament",
                "self_concept",
            ]
        ),
    ),
    ConceptCluster(
        key="autonomy_boundaries",
        domains=frozenset({"values", "goals", "relationships", "voice"}),
        phrases=_phrase_values(
            [
                "where do i need space",
                "how independent am i",
                "my boundaries",
            ]
        ),
        synonyms=_normalize_values(
            [
                "autonomy",
                "independence",
                "independent",
                "boundary",
                "boundaries",
                "space",
                "privacy",
                "control",
                "ownership",
                "self_directed",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "autonomy_need",
                "boundary_style",
                "privacy_preference",
                "independence_style",
                "self_direction",
            ]
        ),
    ),
    ConceptCluster(
        key="values_priorities",
        domains=frozenset({"values"}),
        phrases=_phrase_values(["what matters to me", "what matters most", "my values"]),
        synonyms=_normalize_values(
            [
                "value",
                "values",
                "principle",
                "principles",
                "integrity",
                "priority",
                "important",
                "matter",
                "ethic",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "core_values",
                "decision_principles",
                "non_negotiables",
                "guiding_principles",
            ]
        ),
    ),
    ConceptCluster(
        key="structure_rhythm",
        domains=frozenset({"goals", "patterns", "voice"}),
        phrases=_phrase_values(
            [
                "how i stay organized",
                "how i plan",
                "my routine",
            ]
        ),
        synonyms=_normalize_values(
            [
                "structure",
                "structured",
                "organize",
                "organized",
                "planning",
                "planful",
                "routine",
                "predictable",
                "clarity",
                "cadence",
                "rhythm",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "planning_style",
                "structure_preference",
                "routine_need",
                "workflow_rhythm",
                "cadence_preference",
            ]
        ),
    ),
    ConceptCluster(
        key="patterns_habits",
        domains=frozenset({"patterns"}),
        phrases=_phrase_values(["what do i tend to do", "my patterns", "my habits"]),
        synonyms=_normalize_values(
            [
                "pattern",
                "patterns",
                "habit",
                "habits",
                "routine",
                "tendency",
                "default",
                "usually",
                "often",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "behavior_pattern",
                "default_response",
                "habit_loop",
                "recurring_pattern",
            ]
        ),
    ),
    ConceptCluster(
        key="voice_expression",
        domains=frozenset({"voice"}),
        phrases=_phrase_values(["sound like me", "my voice", "how i write"]),
        synonyms=_normalize_values(
            [
                "voice",
                "tone",
                "writing",
                "style",
                "phrasing",
                "expression",
                "communicate",
                "wording",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "voice_style",
                "writing_tone",
                "communication_style",
                "expression_preference",
            ]
        ),
    ),
    ConceptCluster(
        key="relationships_connection",
        domains=frozenset({"relationships"}),
        phrases=_phrase_values(["how do i connect", "my relationships", "who do i trust"]),
        synonyms=_normalize_values(
            [
                "relationship",
                "relationships",
                "connection",
                "trust",
                "closeness",
                "attachment",
                "bond",
                "people",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "relationship_style",
                "trust_pattern",
                "attachment_style",
                "connection_needs",
            ]
        ),
    ),
    ConceptCluster(
        key="fears_stress",
        domains=frozenset({"fears", "patterns"}),
        phrases=_phrase_values(["what am i afraid of", "what overwhelms me", "what stresses me"]),
        synonyms=_normalize_values(
            [
                "fear",
                "fears",
                "afraid",
                "anxious",
                "worry",
                "stress",
                "overwhelm",
                "pressure",
                "avoid",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "fear_of_failure",
                "stress_trigger",
                "overwhelm_trigger",
                "avoidance_pattern",
            ]
        ),
    ),
    ConceptCluster(
        key="beliefs_worldview",
        domains=frozenset({"beliefs"}),
        phrases=_phrase_values(["what do i believe", "my worldview", "how do i see the world"]),
        synonyms=_normalize_values(
            [
                "belief",
                "beliefs",
                "worldview",
                "perspective",
                "outlook",
                "philosophy",
                "conviction",
                "view",
            ]
        ),
        label_aliases=_normalize_values(
            [
                "core_belief",
                "guiding_belief",
                "worldview",
                "perspective_on_people",
            ]
        ),
    ),
)


def _cluster_applies(cluster: ConceptCluster, *, domain_hints: set[str], query_text: str) -> bool:
    if domain_hints and cluster.domains and not cluster.domains.intersection(domain_hints):
        return False
    if cluster.phrases and find_matching_phrases(query_text, tuple(cluster.phrases)):
        return True
    return False


def expand_query_tokens(
    query_tokens: set[str],
    *,
    domain_hints: list[str] | None = None,
    query_text: str | None = None,
) -> set[str]:
    """Return query tokens expanded with deterministic domain-aware aliases."""
    if not query_tokens:
        return query_tokens

    normalized_text = " ".join((query_text or "").lower().split())
    hinted_domains = {domain for domain in (domain_hints or []) if domain}
    expanded = set(query_tokens)

    for cluster in _CLUSTERS:
        if query_tokens.intersection(cluster.synonyms):
            expanded.update(cluster.synonyms)
            expanded.update(cluster.label_aliases)
            continue
        if _cluster_applies(cluster, domain_hints=hinted_domains, query_text=normalized_text):
            expanded.update(cluster.synonyms)
            expanded.update(cluster.label_aliases)

    return expanded


def concept_alias_tokens_for_text(text: str) -> set[str]:
    """Return deterministic concept alias tokens for one text blob."""
    tokens = tokenize(text)
    return expand_query_tokens(tokens, query_text=text)


def matching_concept_keys_for_text(
    text: str,
    *,
    domain: str | None = None,
) -> set[str]:
    """Return concept-cluster keys that match one text blob."""
    normalized_text = " ".join(text.lower().split())
    tokens = tokenize(text)
    if not tokens and not normalized_text:
        return set()

    domain_hints = {domain} if domain else set()
    matched: set[str] = set()
    for cluster in _CLUSTERS:
        if domain_hints and cluster.domains and not cluster.domains.intersection(domain_hints):
            continue
        alias_tokens: set[str] = set()
        for alias in cluster.label_aliases:
            alias_tokens.update(tokenize(alias.replace("_", " ")))
        if tokens.intersection(cluster.synonyms) or tokens.intersection(alias_tokens):
            matched.add(cluster.key)
            continue
        if cluster.phrases and find_matching_phrases(normalized_text, tuple(cluster.phrases)):
            matched.add(cluster.key)
    return matched


def describe_concept_key(key: str) -> str:
    """Return a compact human-readable label for a concept cluster."""
    return key.replace("_", " ")
