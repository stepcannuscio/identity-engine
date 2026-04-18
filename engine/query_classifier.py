"""Deterministic query classification for retrieval and source routing."""

from dataclasses import dataclass
import re

from engine.text_utils import contains_any_phrase, find_matching_phrases, tokenize

DIRECT_DOMAIN_KEYWORDS = {
    "goal",
    "goals",
    "value",
    "values",
    "personality",
    "pattern",
    "patterns",
    "voice",
    "relationship",
    "relationships",
    "fear",
    "fears",
    "belief",
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
    "diary",
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
    "reference doc",
    "reference docs",
    "transcript",
    "transcripts",
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

VOICE_ADAPTATION_PHRASES = (
    "sound like me",
    "my voice",
    "my tone",
    "in my style",
    "using my style",
)

PLANNING_VERBS = {
    "choose",
    "decide",
    "draft",
    "pick",
    "plan",
    "prioritize",
    "recommend",
    "rewrite",
    "schedule",
    "select",
    "suggest",
}

SELF_REFERENTIAL_TOKENS = {"i", "me", "my", "myself"}

DOMAIN_HINT_TERMS = {
    "goals": ("goal", "goals", "plan", "plans", "priority", "priorities", "toward"),
    "values": ("value", "values", "principle", "principles", "matters"),
    "personality": ("personality", "temperament", "style", "trait", "traits"),
    "patterns": ("pattern", "patterns", "habit", "habits", "routine", "routines"),
    "voice": ("voice", "tone", "writing", "write", "email", "draft"),
    "relationships": ("relationship", "relationships", "friend", "friends", "trust"),
    "fears": ("fear", "fears", "anxious", "anxiety", "avoid", "risk"),
    "beliefs": ("belief", "beliefs", "worldview", "worldviews", "opinion", "opinions"),
}

_WORD_RE = re.compile(r"[a-zA-Z0-9']+")


@dataclass(frozen=True)
class QueryPlan:
    """Deterministic query routing result."""

    retrieval_mode: str
    source_profile: str
    intent_tags: list[str]
    domain_hints: list[str]
    classification_reason: str


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


def _normalized_tokens(query: str) -> set[str]:
    return tokenize(query)


def _extract_domain_hints(query: str, tokens: set[str]) -> list[str]:
    lowered = query.lower()
    hints: list[str] = []
    for domain, terms in DOMAIN_HINT_TERMS.items():
        if domain in tokens or any(term in tokens for term in terms):
            hints.append(domain)
            continue
        if contains_any_phrase(lowered, terms):
            hints.append(domain)
    return sorted(set(hints))


def _intent_tags(query: str, tokens: set[str], domain_hints: list[str]) -> list[str]:
    lowered = query.lower()
    tags: list[str] = []

    if any(term in lowered for term in EVIDENCE_BASED_TERMS):
        tags.append("artifact_lookup")
    if any(phrase in lowered for phrase in VOICE_ADAPTATION_PHRASES):
        tags.append("voice_adaptation")
    if tokens.intersection({"draft", "email", "rewrite", "message", "tone", "write", "writing"}):
        tags.append("writing_task")
    if tokens.intersection(PLANNING_VERBS):
        tags.append("planning")
    if tokens.intersection({"choose", "decide", "pick", "recommend", "select", "suggest"}):
        tags.append("decision_support")
    if contains_any_phrase(lowered, SELF_QUESTION_PATTERNS) or any(
        hint in {"goals", "values", "personality", "patterns", "beliefs", "fears", "relationships"}
        for hint in domain_hints
    ):
        tags.append("self_model")

    # Planning usefulness depends heavily on goal coverage even when the user
    # does not mention the goals domain explicitly.
    if "planning" in tags and "goals" not in domain_hints:
        domain_hints = [*domain_hints, "goals"]

    if "planning" in tags and "patterns" not in domain_hints:
        domain_hints = [*domain_hints, "patterns"]

    return sorted(set(tags))


def _classification_reason(
    query: str,
    *,
    source_profile: str,
    intent_tags: list[str],
    domain_hints: list[str],
) -> str:
    lowered = query.lower()
    matched_self = find_matching_phrases(lowered, SELF_QUESTION_PATTERNS)
    matched_voice = find_matching_phrases(lowered, VOICE_ADAPTATION_PHRASES)
    if source_profile == "evidence_based":
        return "matched explicit artifact/evidence cues"
    if source_profile == "preference_sensitive" and matched_voice:
        return "matched voice-adaptation phrasing"
    if source_profile == "preference_sensitive" and "planning" in intent_tags:
        return "matched planning/decision verbs without artifact lookup cues"
    if source_profile == "self_question" and matched_self:
        return f"matched self-reflection pattern: {matched_self[0]}"
    if source_profile == "self_question" and domain_hints:
        return f"matched identity domain hints: {', '.join(domain_hints[:2])}"
    return "fell back to balanced general query handling"


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
    words = _normalized_tokens(normalized)
    has_self_reference = bool(words.intersection(SELF_REFERENTIAL_TOKENS))
    has_artifact_lookup = any(term in normalized for term in EVIDENCE_BASED_TERMS)
    has_voice_adaptation = any(phrase in normalized for phrase in VOICE_ADAPTATION_PHRASES)
    has_preference_task = bool(words.intersection(PLANNING_VERBS)) or has_voice_adaptation
    has_self_pattern = any(pattern in normalized for pattern in SELF_QUESTION_PATTERNS)
    has_domain_question = bool(words.intersection(DIRECT_DOMAIN_KEYWORDS)) and has_self_reference

    if has_artifact_lookup:
        return "evidence_based"

    if has_preference_task or any(term in normalized for term in PREFERENCE_SENSITIVE_TERMS):
        return "preference_sensitive"

    if has_self_pattern or has_domain_question:
        return "self_question"

    if has_self_reference and words.intersection(DIRECT_DOMAIN_KEYWORDS):
        return "self_question"

    return "general"


def build_query_plan(query: str) -> QueryPlan:
    """Return both the public retrieval mode and the internal source profile."""
    tokens = _normalized_tokens(query)
    domain_hints = _extract_domain_hints(query, tokens)
    intent_tags = _intent_tags(query, tokens, domain_hints)
    if "planning" in intent_tags:
        domain_hints = sorted(set([*domain_hints, "goals", "patterns"]))
    source_profile = classify_source_profile(query)
    return QueryPlan(
        retrieval_mode=classify_query(query),
        source_profile=source_profile,
        intent_tags=intent_tags,
        domain_hints=domain_hints,
        classification_reason=_classification_reason(
            query,
            source_profile=source_profile,
            intent_tags=intent_tags,
            domain_hints=domain_hints,
        ),
    )
