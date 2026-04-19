"""Structured context assembly for inference tasks.

This module packages retrieved identity data into a typed object that can be
passed through privacy checks and prompt rendering without spreading selection
logic across multiple layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any, cast

from engine.artifact_retrieval import retrieve_artifact_chunk_candidates
from engine.preference_summary import (
    PreferenceSummaryItem,
    PreferenceSummaryPayload,
    empty_preference_summary,
    get_relevant_preference_context,
)
from engine.retriever import DOMAIN_KEYWORDS, budget_for_query_type, retrieve_attribute_candidates
from engine.session import HISTORY_CAP
from engine.voice_profile import VoiceProfile, build_voice_profile

_TOKEN_RE = re.compile(r"[a-z0-9']+")

_FINAL_EVIDENCE_CAPS = {
    "simple": 8,
    "open_ended": 12,
}

_ARTIFACT_CANDIDATE_LIMITS = {
    "simple": 6,
    "open_ended": 10,
}

_SOURCE_PROFILE_CONFIG = {
    "self_question": {
        "weights": {"identity": 1.00, "preference": 0.55, "artifact": 0.25},
        "caps": {"identity": 6, "preference": 2, "artifact": 1},
    },
    "evidence_based": {
        "weights": {"identity": 0.75, "preference": 0.35, "artifact": 1.00},
        "caps": {"identity": 3, "preference": 1, "artifact": 4},
    },
    "preference_sensitive": {
        "weights": {"identity": 0.70, "preference": 1.00, "artifact": 0.30},
        "caps": {"identity": 3, "preference": 4, "artifact": 1},
    },
    "voice_generation": {
        "weights": {"identity": 0.90, "preference": 1.00, "artifact": 0.75},
        "caps": {"identity": 4, "preference": 4, "artifact": 2},
    },
    "general": {
        "weights": {"identity": 1.00, "preference": 0.70, "artifact": 0.45},
        "caps": {"identity": 5, "preference": 2, "artifact": 2},
    },
}

_SOURCE_ORDER = {
    "identity": 3,
    "preference": 2,
    "artifact": 1,
}

_PREFERENCE_INFERRED_SOURCES = frozenset({"inferred", "system_inference"})


@dataclass(frozen=True)
class EvidenceItem:
    """One normalized evidence candidate or selected prompt item."""

    source_type: str
    kind: str
    raw_score: float
    normalized_score: float
    final_score: float
    domain: str | None
    routing: str
    status: str
    title_or_label: str
    content: str
    item_id: str
    source: str | None = None
    artifact_id: str | None = None


@dataclass(frozen=True)
class AssembledContext:
    """Inference-ready identity context for one task."""

    task_type: str
    input_text: str
    attributes: list[dict]
    session_history: list[dict]
    domains_used: list[str]
    attribute_count: int
    retrieval_mode: str
    source_profile: str = "general"
    intent_tags: list[str] = field(default_factory=list)
    domain_hints: list[str] = field(default_factory=list)
    was_trimmed: bool = False
    contains_local_only: bool = False
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    preference_attributes: list[dict] = field(default_factory=list)
    preference_summary: PreferenceSummaryPayload = field(
        default_factory=empty_preference_summary
    )
    preference_count: int = 0
    preference_categories_used: list[str] = field(default_factory=list)
    artifact_chunks: list[dict] = field(default_factory=list)
    artifact_count: int = 0
    artifact_sources: list[str] = field(default_factory=list)
    voice_profile: VoiceProfile | None = None
    budget_metadata: dict[str, int | float | str] = field(default_factory=dict)


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _query_domains(query: str) -> set[str]:
    lowered = query.lower()
    matched: set[str] = set()
    for domain, triggers in DOMAIN_KEYWORDS.items():
        if any(trigger in lowered for trigger in triggers):
            matched.add(domain)
    return matched


def _resolved_source_config(source_profile: str, intent_tags: list[str]) -> dict[str, dict[str, float | int]]:
    base = _SOURCE_PROFILE_CONFIG[source_profile]
    weights = dict(base["weights"])
    caps = dict(base["caps"])

    if source_profile == "preference_sensitive" and "planning" in intent_tags:
        weights.update({"identity": 0.85, "preference": 1.00, "artifact": 0.25})
        caps.update({"identity": 4, "preference": 3, "artifact": 1})

    if source_profile == "preference_sensitive" and "voice_adaptation" in intent_tags:
        weights.update({"identity": 0.55, "preference": 1.00, "artifact": 0.20})
        caps.update({"identity": 2, "preference": 4, "artifact": 1})

    if source_profile == "voice_generation":
        weights.update({"identity": 0.95, "preference": 1.00, "artifact": 0.80})
        caps.update({"identity": 4, "preference": 4, "artifact": 2})

    return {"weights": weights, "caps": caps}


def _cap_session_history(history: list[dict]) -> tuple[list[dict], bool]:
    if not history:
        return [], False

    max_messages = HISTORY_CAP * 2
    trimmed = len(history) > max_messages
    return history[-max_messages:], trimmed


def _preference_content(attribute: dict) -> str:
    value = str(attribute.get("value", "")).strip()
    elaboration = str(attribute.get("elaboration", "") or "").strip()
    if value and elaboration:
        return f"{value} {elaboration}"
    return value or elaboration


def _collect_identity_candidates(attributes: list[dict]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for index, attribute in enumerate(attributes):
        label = str(attribute.get("label", "")).strip() or f"identity_{index}"
        items.append(
            EvidenceItem(
                source_type="identity",
                kind="attribute",
                raw_score=float(attribute.get("score", 0.0) or 0.0),
                normalized_score=0.0,
                final_score=0.0,
                domain=str(attribute.get("domain", "")) or None,
                routing=str(attribute.get("routing", "local_only")),
                status=str(attribute.get("status", "active")),
                title_or_label=label,
                content=_preference_content(attribute),
                item_id=f"identity:{attribute.get('id', label)}",
                source=str(attribute.get("source", "")) or None,
            )
        )
    return items


def _collect_preference_candidates(
    preference_attributes: list[dict],
    signal_items: list[PreferenceSummaryItem],
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for index, attribute in enumerate(preference_attributes):
        label = str(attribute.get("label", "")).strip() or f"preference_{index}"
        items.append(
            EvidenceItem(
                source_type="preference",
                kind="attribute",
                raw_score=float(attribute.get("score", 0.0) or 0.0),
                normalized_score=0.0,
                final_score=0.0,
                domain=str(attribute.get("domain", "")) or None,
                routing=str(attribute.get("routing", "local_only")),
                status=str(attribute.get("status", "active")),
                title_or_label=label,
                content=_preference_content(attribute),
                item_id=f"preference-attribute:{attribute.get('id', label)}",
                source=str(attribute.get("source", "")) or None,
            )
        )
    for index, item in enumerate(signal_items):
        title = str(item.get("subject", item.get("summary", f"signal_{index}"))).strip()
        items.append(
            EvidenceItem(
                source_type="preference",
                kind=str(item.get("source", "signal_summary")),
                raw_score=float(item.get("score", 0.0) or 0.0),
                normalized_score=0.0,
                final_score=0.0,
                domain=str(item.get("category", "")) or None,
                routing=str(item.get("routing", "local_only")),
                status=str(item.get("status", "summary")),
                title_or_label=title,
                content=str(item.get("summary", "")).strip(),
                item_id=f"preference-signal:{title}:{index}",
                source=str(item.get("source", "signal_summary")),
            )
        )
    return items


def _collect_artifact_candidates(chunks: list[dict]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for index, chunk in enumerate(chunks):
        title = str(chunk.get("title", "Untitled artifact")).strip() or "Untitled artifact"
        artifact_id = str(chunk.get("artifact_id", chunk.get("id", f"artifact_{index}")))
        items.append(
            EvidenceItem(
                source_type="artifact",
                kind="artifact_chunk",
                raw_score=float(chunk.get("score", 0.0) or 0.0),
                normalized_score=0.0,
                final_score=0.0,
                domain=str(chunk.get("domain", "")) or None,
                routing=str(chunk.get("routing", "local_only")),
                status="supporting",
                title_or_label=title,
                content=str(chunk.get("content", "")).strip(),
                item_id=f"artifact:{chunk.get('id', artifact_id)}",
                source=title,
                artifact_id=artifact_id,
            )
        )
    return items


def _normalize_candidates(candidates: list[EvidenceItem]) -> list[EvidenceItem]:
    max_scores: dict[str, float] = {}
    for item in candidates:
        max_scores[item.source_type] = max(
            max_scores.get(item.source_type, 0.0),
            float(item.raw_score),
        )

    normalized: list[EvidenceItem] = []
    for item in candidates:
        denom = max_scores.get(item.source_type, 0.0)
        normalized_score = 0.0 if denom <= 0 else min(float(item.raw_score) / denom, 1.0)
        normalized.append(replace(item, normalized_score=round(normalized_score, 4)))
    return normalized


def _trust_bonus(item: EvidenceItem) -> float:
    if item.source_type == "artifact":
        return 0.0
    if item.status == "confirmed":
        return 0.15
    if item.source_type == "preference" and item.kind == "attribute":
        if (item.source or "") in _PREFERENCE_INFERRED_SOURCES:
            return 0.08
    if item.status == "active":
        return 0.05
    return 0.0


def _profile_bonus(item: EvidenceItem, source_profile: str) -> float:
    profile_source_map = {
        "self_question": "identity",
        "evidence_based": "artifact",
        "preference_sensitive": "preference",
    }
    if source_profile == "voice_generation":
        if item.source_type == "preference":
            return 0.15
        if item.source_type == "identity" and item.domain == "voice":
            return 0.14
        if item.source_type == "artifact" and item.domain == "voice":
            return 0.12
    if profile_source_map.get(source_profile) == item.source_type:
        return 0.15
    return 0.0


def _domain_match_bonus(
    item: EvidenceItem,
    matched_domains: set[str],
    task_profiles: list[str],
) -> float:
    if item.domain and item.domain in matched_domains:
        return 0.10
    if item.source_type == "preference" and task_profiles:
        item_text = " ".join(
            part
            for part in [item.domain or "", item.title_or_label, item.content]
            if part
        ).lower()
        if any(profile.replace("_", " ") in item_text for profile in task_profiles):
            return 0.10
    return 0.0


def _effective_score(
    item: EvidenceItem,
    *,
    source_profile: str,
    matched_domains: set[str],
    task_profiles: list[str],
    intent_tags: list[str],
    selected_artifact_ids: set[str],
    selected_artifact_sources: set[str],
    top_confirmed_identity_score: float | None,
) -> float:
    config = _resolved_source_config(source_profile, intent_tags)
    score = item.normalized_score * float(config["weights"][item.source_type])
    score += _trust_bonus(item)
    score += _profile_bonus(item, source_profile)
    score += _domain_match_bonus(item, matched_domains, task_profiles)

    if source_profile == "evidence_based" and item.source_type == "artifact":
        if (item.source or item.title_or_label) not in selected_artifact_sources:
            score += 0.10

    if source_profile == "voice_generation" and item.domain == "voice":
        score += 0.08

    if item.source_type == "artifact" and item.artifact_id in selected_artifact_ids:
        score -= 0.15

    if (
        source_profile == "self_question"
        and item.source_type == "artifact"
        and top_confirmed_identity_score is not None
    ):
        score = min(score, top_confirmed_identity_score - 0.01)

    return round(score, 4)


def _select_evidence_items(
    candidates: list[EvidenceItem],
    *,
    query_type: str,
    source_profile: str,
    matched_domains: set[str],
    task_profiles: list[str],
    intent_tags: list[str],
) -> list[EvidenceItem]:
    if not candidates:
        return []

    normalized = _normalize_candidates(candidates)
    config = _resolved_source_config(source_profile, intent_tags)
    total_cap = _FINAL_EVIDENCE_CAPS[query_type]
    source_caps = dict(config["caps"])

    top_confirmed_identity_score: float | None = None
    confirmed_identity_scores = [
        _effective_score(
            item,
            source_profile=source_profile,
            matched_domains=matched_domains,
            task_profiles=task_profiles,
            intent_tags=intent_tags,
            selected_artifact_ids=set(),
            selected_artifact_sources=set(),
            top_confirmed_identity_score=None,
        )
        for item in normalized
        if item.source_type == "identity" and item.status == "confirmed"
    ]
    if confirmed_identity_scores:
        top_confirmed_identity_score = max(confirmed_identity_scores)

    selected: list[EvidenceItem] = []
    selected_ids: set[str] = set()
    selected_counts = {"identity": 0, "preference": 0, "artifact": 0}
    selected_artifact_ids: set[str] = set()
    selected_artifact_sources: set[str] = set()

    while len(selected) < total_cap:
        best_item: EvidenceItem | None = None
        best_score: float | None = None
        best_tiebreak: tuple[float, int, float, str] | None = None

        for item in normalized:
            if item.item_id in selected_ids:
                continue
            if selected_counts[item.source_type] >= int(source_caps[item.source_type]):
                continue
            if (
                source_profile != "evidence_based"
                and item.source_type == "artifact"
                and item.artifact_id in selected_artifact_ids
            ):
                continue

            effective_score = _effective_score(
                item,
                source_profile=source_profile,
                matched_domains=matched_domains,
                task_profiles=task_profiles,
                intent_tags=intent_tags,
                selected_artifact_ids=selected_artifact_ids,
                selected_artifact_sources=selected_artifact_sources,
                top_confirmed_identity_score=top_confirmed_identity_score,
            )
            tiebreak = (
                effective_score,
                _SOURCE_ORDER[item.source_type],
                item.raw_score,
                item.title_or_label,
            )
            if best_tiebreak is None or tiebreak > best_tiebreak:
                best_item = item
                best_score = effective_score
                best_tiebreak = tiebreak

        if best_item is None or best_score is None:
            break

        selected_item = replace(best_item, final_score=best_score)
        selected.append(selected_item)
        selected_ids.add(selected_item.item_id)
        selected_counts[selected_item.source_type] += 1
        if selected_item.source_type == "artifact":
            if selected_item.artifact_id:
                selected_artifact_ids.add(selected_item.artifact_id)
            selected_artifact_sources.add(selected_item.source or selected_item.title_or_label)

    return sorted(
        selected,
        key=lambda item: (
            item.final_score,
            _SOURCE_ORDER[item.source_type],
            item.raw_score,
            item.title_or_label,
        ),
        reverse=True,
    )


def _project_selected_attributes(
    selected_items: list[EvidenceItem],
    attribute_candidates: list[dict],
) -> list[dict]:
    selected_ids = {
        item.item_id.removeprefix("identity:")
        for item in selected_items
        if item.source_type == "identity"
    }
    selected_attributes: list[dict] = []
    for attribute in attribute_candidates:
        attr_id = str(attribute.get("id", ""))
        if attr_id in selected_ids:
            selected_attributes.append(attribute)
    return selected_attributes


def _project_selected_preference_attributes(
    selected_items: list[EvidenceItem],
    preference_attributes: list[dict],
) -> list[dict]:
    selected_ids = {
        item.item_id.removeprefix("preference-attribute:")
        for item in selected_items
        if item.source_type == "preference" and item.kind == "attribute"
    }
    selected_attributes: list[dict] = []
    for attribute in preference_attributes:
        attr_id = str(attribute.get("id", ""))
        if attr_id in selected_ids:
            selected_attributes.append(attribute)
    return selected_attributes


def _project_selected_artifact_chunks(
    selected_items: list[EvidenceItem],
    artifact_candidates: list[dict],
) -> list[dict]:
    selected_ids = {
        item.item_id.removeprefix("artifact:")
        for item in selected_items
        if item.source_type == "artifact"
    }
    selected_chunks: list[dict] = []
    for chunk in artifact_candidates:
        chunk_id = str(chunk.get("id", ""))
        if chunk_id in selected_ids:
            selected_chunks.append(chunk)
    return selected_chunks


def assemble_query_context(
    query: str,
    query_type: str,
    source_profile: str | list[dict],
    session_history: list[dict] | Any,
    conn: Any = None,
    *,
    intent_tags: list[str] | None = None,
    domain_hints: list[str] | None = None,
) -> AssembledContext:
    """Assemble structured context for grounded query inference."""
    if conn is None:
        resolved_source_profile = "general"
        resolved_session_history = cast(list[dict], source_profile)
        resolved_conn = session_history
    else:
        resolved_source_profile = str(source_profile)
        resolved_session_history = cast(list[dict], session_history)
        resolved_conn = conn
    resolved_intent_tags = sorted(set(intent_tags or []))
    resolved_domain_hints = sorted(set(domain_hints or []))

    attribute_candidates = retrieve_attribute_candidates(
        query,
        query_type,
        resolved_conn,
        domain_hints=resolved_domain_hints,
        intent_tags=resolved_intent_tags,
    )
    preference_context = get_relevant_preference_context(
        query,
        query_type,
        resolved_conn,
        domain_hints=resolved_domain_hints,
        intent_tags=resolved_intent_tags,
    )
    artifact_candidates = retrieve_artifact_chunk_candidates(
        resolved_conn,
        query,
        limit=_ARTIFACT_CANDIDATE_LIMITS[query_type],
        domain_hints=resolved_domain_hints,
    )

    matched_domains = set(resolved_domain_hints) or _query_domains(query)
    task_profiles = list(preference_context.summary["task_profiles"])
    evidence_candidates = (
        _collect_identity_candidates(attribute_candidates)
        + _collect_preference_candidates(
            preference_context.attributes,
            preference_context.signal_items,
        )
        + _collect_artifact_candidates(artifact_candidates)
    )
    evidence_items = _select_evidence_items(
        evidence_candidates,
        query_type=query_type,
        source_profile=resolved_source_profile,
        matched_domains=matched_domains,
        task_profiles=task_profiles,
        intent_tags=resolved_intent_tags,
    )

    attributes = _project_selected_attributes(evidence_items, attribute_candidates)
    preference_attributes = _project_selected_preference_attributes(
        evidence_items,
        preference_context.attributes,
    )
    artifact_chunks = _project_selected_artifact_chunks(evidence_items, artifact_candidates)

    capped_history, history_was_trimmed = _cap_session_history(resolved_session_history)
    domains_used = sorted(
        {
            item.domain
            for item in evidence_items
            if item.domain
        }
    )
    budget = budget_for_query_type(query_type)
    contains_local_only = any(
        attribute.get("routing") == "local_only" for attribute in attributes
    ) or any(
        attribute.get("routing") == "local_only" for attribute in preference_attributes
    ) or bool(artifact_chunks)
    artifact_sources = sorted(
        {
            str(chunk.get("title", "")).strip()
            for chunk in artifact_chunks
            if str(chunk.get("title", "")).strip()
        }
    )
    voice_profile = build_voice_profile(
        source_profile=resolved_source_profile,
        attributes=attributes,
        preference_attributes=preference_attributes,
        preference_summary=preference_context.summary,
        artifact_chunks=artifact_chunks,
    )
    was_trimmed = (
        history_was_trimmed
        or len(attribute_candidates) > len(attributes)
        or preference_context.was_trimmed
        or len(artifact_candidates) > len(artifact_chunks)
        or len(evidence_candidates) > len(evidence_items)
    )

    return AssembledContext(
        task_type="query",
        input_text=query,
        attributes=attributes,
        session_history=capped_history,
        domains_used=domains_used,
        attribute_count=len(attributes),
        retrieval_mode=query_type,
        source_profile=resolved_source_profile,
        intent_tags=resolved_intent_tags,
        domain_hints=resolved_domain_hints,
        was_trimmed=was_trimmed,
        contains_local_only=contains_local_only,
        evidence_items=evidence_items,
        preference_attributes=preference_attributes,
        preference_summary=preference_context.summary,
        preference_count=len(preference_attributes) + len(preference_context.signal_items),
        preference_categories_used=preference_context.categories_used,
        artifact_chunks=artifact_chunks,
        artifact_count=len(artifact_chunks),
        artifact_sources=artifact_sources,
        voice_profile=voice_profile,
        budget_metadata={
            "source_profile": resolved_source_profile,
            "intent_tags": ",".join(resolved_intent_tags),
            "domain_hints": ",".join(resolved_domain_hints),
            "max_attributes": int(budget["max_attributes"]),
            "max_domains": int(budget["max_domains"]),
            "score_threshold": float(budget["score_threshold"]),
            "history_cap_messages": HISTORY_CAP * 2,
            "max_artifact_candidates": _ARTIFACT_CANDIDATE_LIMITS[query_type],
            "max_evidence_items": _FINAL_EVIDENCE_CAPS[query_type],
            **preference_context.budget_metadata,
        },
    )
