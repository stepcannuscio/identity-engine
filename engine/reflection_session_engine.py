"""Deep reflection session engine for multi-turn Socratic identity exploration."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from config.llm_router import ProviderConfig
from engine.privacy_broker import PrivacyBroker


_MAX_HISTORY_TURNS = 10


@dataclass
class ReflectionSeed:
    """Optimal starting context for a reflection session."""

    seed_domain: str | None
    seed_question: str
    pending_contradictions: list[dict[str, Any]]
    pending_syntheses: list[dict[str, Any]]
    drift_domains: list[str]


@dataclass
class SuggestedAttributeUpdate:
    """One suggested attribute update from a reflection turn."""

    domain: str
    label: str
    value: str
    confidence: float
    elaboration: str | None


@dataclass
class ReflectionTurnResult:
    """Result of processing one reflection turn."""

    next_question: str
    suggested_updates: list[SuggestedAttributeUpdate]
    themes_noticed: list[str]
    staged_signal_ids: list[str]


@dataclass
class ReflectionSessionState:
    """In-memory state for one active reflection session."""

    session_id: str
    history: list[dict[str, str]]
    domains_explored: list[str]
    themes_noticed: list[str]
    seed_domain: str | None
    turn_count: int
    started_at: str
    staged_signal_ids: list[str] = field(default_factory=list)


def build_reflection_session_seed(conn: Any) -> ReflectionSeed:
    """Find the best starting point using Phase 4+5 data (contradictions, syntheses, drift)."""
    contradiction_rows = conn.execute(
        """
        SELECT cf.id, a.value AS a_value, b.value AS b_value, d.name AS domain_name
        FROM contradiction_flags cf
        JOIN attributes a ON a.id = cf.attribute_a_id
        JOIN attributes b ON b.id = cf.attribute_b_id
        JOIN domains d ON d.id = a.domain_id
        WHERE cf.status = 'pending'
        ORDER BY cf.confidence DESC
        LIMIT 3
        """
    ).fetchall()
    pending_contradictions: list[dict[str, Any]] = [
        {
            "id": str(row[0]),
            "attribute_a_value": str(row[1]),
            "attribute_b_value": str(row[2]),
            "domain": str(row[3]),
        }
        for row in contradiction_rows
    ]

    synthesis_rows = conn.execute(
        """
        SELECT id, theme_label, domains_involved_json, strength
        FROM cross_domain_synthesis
        WHERE status = 'pending_review'
        ORDER BY strength DESC
        LIMIT 3
        """
    ).fetchall()
    pending_syntheses: list[dict[str, Any]] = []
    for row in synthesis_rows:
        try:
            domains: list[str] = json.loads(str(row[2]))
        except (json.JSONDecodeError, TypeError):
            domains = []
        pending_syntheses.append(
            {
                "id": str(row[0]),
                "theme_label": str(row[1]),
                "domains_involved": domains,
                "strength": float(row[3]),
            }
        )

    drift_rows = conn.execute(
        """
        SELECT domain FROM temporal_events
        WHERE event_type = 'drift' AND status = 'active'
        ORDER BY detected_at DESC
        LIMIT 3
        """
    ).fetchall()
    drift_domains = [str(row[0]) for row in drift_rows]

    seed_domain: str | None = None
    if pending_contradictions:
        seed_domain = pending_contradictions[0]["domain"]
    elif pending_syntheses and pending_syntheses[0]["domains_involved"]:
        seed_domain = pending_syntheses[0]["domains_involved"][0]
    elif drift_domains:
        seed_domain = drift_domains[0]
    else:
        row = conn.execute(
            """
            SELECT d.name FROM domains d
            JOIN attributes a ON a.domain_id = d.id
            WHERE a.status IN ('active', 'confirmed')
            GROUP BY d.name
            ORDER BY COUNT(a.id) DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            seed_domain = str(row[0])

    seed_question = _build_seed_question(
        seed_domain, pending_contradictions, pending_syntheses, drift_domains
    )
    return ReflectionSeed(
        seed_domain=seed_domain,
        seed_question=seed_question,
        pending_contradictions=pending_contradictions,
        pending_syntheses=pending_syntheses,
        drift_domains=drift_domains,
    )


def _build_seed_question(
    seed_domain: str | None,
    contradictions: list[dict[str, Any]],
    syntheses: list[dict[str, Any]],
    drift_domains: list[str],
) -> str:
    if contradictions:
        c = contradictions[0]
        return (
            f"You've described yourself as both \"{c['attribute_a_value']}\" and "
            f"\"{c['attribute_b_value']}\". What feels most true right now?"
        )
    if syntheses:
        s = syntheses[0]
        domains_text = ", ".join(s["domains_involved"][:3])
        return (
            f"A {s['theme_label']} thread appears across your {domains_text}. "
            "When did you first notice this about yourself?"
        )
    if drift_domains:
        return (
            f"Your {drift_domains[0]} has been changing recently. "
            "What's been shifting for you there?"
        )
    if seed_domain:
        return f"What comes to mind when you think about your {seed_domain} right now?"
    return "What feels most important about who you are right now?"


def _build_reflection_system_prompt() -> str:
    return (
        "You are a Socratic guide helping a user reflect on their identity. "
        "Ask one deep, open-ended question per turn. "
        "After the user responds, consider whether any identity updates are worth noting. "
        "Return compact JSON with exactly these keys:\n"
        "  next_question: string — the next reflective question to ask\n"
        "  suggested_attribute_updates: list of objects with keys "
        "{domain, label, value, confidence, elaboration} — "
        "only include clear self-descriptions the user explicitly stated; "
        "confidence must be between 0.0 and 0.75; keep this list short (0–2 items)\n"
        "  themes_noticed: list of strings — abstract identity themes across the conversation\n"
        "Return only JSON. Do not explain or narrate outside the JSON."
    )


def _build_reflection_context_message(
    conn: Any,
    seed: ReflectionSeed,
    history: list[dict[str, str]],
) -> str:
    lines: list[str] = []

    if seed.seed_domain:
        attr_rows = conn.execute(
            """
            SELECT a.label, a.value FROM attributes a
            JOIN domains d ON d.id = a.domain_id
            WHERE d.name = ? AND a.status IN ('active', 'confirmed')
            ORDER BY a.confidence DESC
            LIMIT 6
            """,
            (seed.seed_domain,),
        ).fetchall()
        if attr_rows:
            lines.append(f"Stored {seed.seed_domain} beliefs:")
            for row in attr_rows:
                lines.append(f"  - {row[0]}: {row[1]}")

    if seed.pending_contradictions:
        c = seed.pending_contradictions[0]
        lines.append(
            f"Tension to explore: \"{c['attribute_a_value']}\" vs \"{c['attribute_b_value']}\""
        )

    if seed.pending_syntheses:
        s = seed.pending_syntheses[0]
        lines.append(
            f"Cross-domain theme: {s['theme_label']} across "
            f"{', '.join(s['domains_involved'][:3])}"
        )

    if seed.drift_domains:
        lines.append(f"Domains in recent flux: {', '.join(seed.drift_domains)}")

    recent = history[-((_MAX_HISTORY_TURNS * 2)):]
    if recent:
        lines.append("\nConversation so far:")
        for turn in recent:
            role = turn["role"].capitalize()
            lines.append(f"  {role}: {turn['content']}")

    lines.append("\nReturn JSON for the next question.")
    return "\n".join(lines)


def _call_reflection_llm(
    conn: Any,
    seed: ReflectionSeed,
    history: list[dict[str, str]],
    provider_config: ProviderConfig,
) -> dict[str, Any] | None:
    """Call PrivacyBroker for one reflection turn. Returns parsed JSON or None."""
    context_text = _build_reflection_context_message(conn, seed, history)
    messages = [
        {"role": "system", "content": _build_reflection_system_prompt()},
        {"role": "user", "content": context_text},
    ]
    domains = [seed.seed_domain] if seed.seed_domain else []
    try:
        result = PrivacyBroker(provider_config).generate_grounded_response(
            messages,
            attributes=[],
            task_type="reflection",
            retrieval_mode="teach",
            contains_local_only_context=bool(seed.seed_domain),
            domains_used=domains,
        )
        if isinstance(result.content, str):
            return _parse_reflection_response(result.content)
    except Exception:
        pass
    return None


def start_reflection_session(
    conn: Any,
    provider_config: ProviderConfig,
) -> tuple[str, ReflectionSessionState, str]:
    """Create a new reflection session. Returns (session_id, state, first_question)."""
    seed = build_reflection_session_seed(conn)
    session_id = str(uuid.uuid4())

    first_question = seed.seed_question
    parsed = _call_reflection_llm(conn, seed, [], provider_config)
    if parsed and parsed.get("next_question"):
        first_question = str(parsed["next_question"])

    state = ReflectionSessionState(
        session_id=session_id,
        history=[{"role": "assistant", "content": first_question}],
        domains_explored=[seed.seed_domain] if seed.seed_domain else [],
        themes_noticed=[],
        seed_domain=seed.seed_domain,
        turn_count=1,
        started_at=datetime.now(UTC).isoformat(),
        staged_signal_ids=[],
    )
    return session_id, state, first_question


def process_reflection_turn(
    conn: Any,
    state: ReflectionSessionState,
    user_message: str,
    provider_config: ProviderConfig,
) -> ReflectionTurnResult:
    """Process one user turn. Stages suggested updates and returns next question."""
    state.history.append({"role": "user", "content": user_message})

    seed = ReflectionSeed(
        seed_domain=state.seed_domain,
        seed_question="",
        pending_contradictions=[],
        pending_syntheses=[],
        drift_domains=[],
    )

    next_question = _fallback_question(state)
    suggested_updates: list[SuggestedAttributeUpdate] = []
    themes_noticed: list[str] = []

    parsed = _call_reflection_llm(conn, seed, state.history[:-1], provider_config)
    if parsed:
        if parsed.get("next_question"):
            next_question = str(parsed["next_question"])

        for raw_update in parsed.get("suggested_attribute_updates", [])[:2]:
            if _valid_update(raw_update):
                confidence = min(float(raw_update.get("confidence", 0.5)), 0.75)
                elab = raw_update.get("elaboration")
                suggested_updates.append(
                    SuggestedAttributeUpdate(
                        domain=str(raw_update["domain"]),
                        label=str(raw_update["label"]),
                        value=str(raw_update["value"]),
                        confidence=confidence,
                        elaboration=str(elab) if elab else None,
                    )
                )
                domain = str(raw_update["domain"])
                if domain not in state.domains_explored:
                    state.domains_explored.append(domain)

        for raw_theme in parsed.get("themes_noticed", [])[:3]:
            theme_str = str(raw_theme).strip()
            if theme_str and theme_str not in state.themes_noticed:
                state.themes_noticed.append(theme_str)
                themes_noticed.append(theme_str)

    staged_ids: list[str] = []
    for update in suggested_updates:
        signal_id = _stage_reflection_signal(conn, state.session_id, update)
        if signal_id is not None:
            staged_ids.append(signal_id)
            state.staged_signal_ids.append(signal_id)

    state.history.append({"role": "assistant", "content": next_question})
    state.turn_count += 1

    return ReflectionTurnResult(
        next_question=next_question,
        suggested_updates=suggested_updates,
        themes_noticed=themes_noticed,
        staged_signal_ids=staged_ids,
    )


def _fallback_question(state: ReflectionSessionState) -> str:
    """Deterministic follow-up when LLM is unavailable."""
    domain = state.seed_domain or "yourself"
    count = state.turn_count
    if count <= 1:
        return f"What's shaped your {domain} the most?"
    if count == 2:
        return "How has that changed over time?"
    if count == 3:
        return "What would you want your future self to know about this?"
    return "Is there anything else here that feels important to you?"


def _parse_reflection_response(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass
    return None


def _valid_update(update: Any) -> bool:
    if not isinstance(update, dict):
        return False
    return bool(update.get("domain") and update.get("label") and update.get("value"))


def _stage_reflection_signal(
    conn: Any,
    session_id: str,
    update: SuggestedAttributeUpdate,
) -> str | None:
    """Write a reflection suggestion to extracted_session_signals for later review."""
    try:
        signal_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        payload: dict[str, Any] = {
            "domain": update.domain,
            "label": update.label,
            "value": update.value,
            "confidence": update.confidence,
            "source_profile": "reflection",
            "query_excerpt": f"reflection_{update.domain}",
        }
        if update.elaboration:
            payload["elaboration"] = update.elaboration
        conn.execute(
            """
            INSERT INTO extracted_session_signals (
                id, session_id, exchange_index, signal_type, payload_json, processed, created_at
            )
            VALUES (?, ?, 0, ?, ?, 0, ?)
            """,
            (signal_id, session_id, "attribute_candidate", json.dumps(payload), now),
        )
        conn.commit()
        return signal_id
    except Exception:
        return None
