"""Teach question planning and persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
import uuid

from config.llm_router import ConfigurationError
from config.llm_router import ProviderConfig
from engine.interview_catalog import DOMAINS
from engine.privacy_broker import PrivacyBroker
from engine.setup_state import resolve_local_provider_config
from engine.synthesis_engine import refresh_cross_domain_intelligence
from engine.temporal_analyzer import refresh_temporal_intelligence

QUESTION_LIMIT = 3
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class TeachQuestion:
    """One planned Teach question."""

    id: str
    prompt: str
    domain: str | None
    intent_key: str
    source: str
    status: str
    priority: float
    onboarding_stage: str


def _slug(value: str) -> str:
    normalized = _NON_WORD_RE.sub("_", value.lower()).strip("_")
    return normalized or "question"


def _normalize_prompt(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _domain_attribute_counts(conn) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT d.name, COUNT(a.id)
        FROM domains d
        LEFT JOIN attributes a ON a.domain_id = d.id AND a.status IN ('active', 'confirmed')
        GROUP BY d.name
        """
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _artifact_tags(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT tag
        FROM artifact_tags
        ORDER BY created_at DESC, tag ASC
        LIMIT 10
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _feedback_counts(conn) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT q.intent_key, COUNT(f.id)
        FROM teach_questions q
        JOIN teach_question_feedback f ON f.question_id = q.id
        GROUP BY q.intent_key
        """
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _seen_intents(conn) -> set[str]:
    rows = conn.execute("SELECT intent_key FROM teach_questions").fetchall()
    return {str(row[0]) for row in rows}


def _seen_prompts(conn) -> set[str]:
    rows = conn.execute("SELECT prompt FROM teach_questions").fetchall()
    return {_normalize_prompt(str(row[0])) for row in rows}


def _prune_stale_pending_questions(conn) -> None:
    """Dismiss duplicate pending questions and stale pending rows for completed intents."""
    rows = conn.execute(
        """
        SELECT id, intent_key, prompt, status
        FROM teach_questions
        ORDER BY updated_at DESC, created_at DESC, id DESC
        """
    ).fetchall()
    completed_intents = {
        str(row[1])
        for row in rows
        if str(row[3]) in {"answered", "dismissed"}
    }
    completed_prompts = {
        _normalize_prompt(str(row[2]))
        for row in rows
        if str(row[3]) in {"answered", "dismissed"}
    }
    kept_pending: set[str] = set()
    kept_pending_prompts: set[str] = set()
    stale_pending_ids: list[str] = []

    for row in rows:
        question_id = str(row[0])
        intent_key = str(row[1])
        prompt = _normalize_prompt(str(row[2]))
        status = str(row[3])
        if status != "pending":
            continue
        if (
            intent_key in completed_intents
            or prompt in completed_prompts
            or intent_key in kept_pending
            or prompt in kept_pending_prompts
        ):
            stale_pending_ids.append(question_id)
            continue
        kept_pending.add(intent_key)
        kept_pending_prompts.add(prompt)

    if not stale_pending_ids:
        return

    now = _now()
    conn.executemany(
        """
        UPDATE teach_questions
        SET status = 'dismissed',
            updated_at = ?
        WHERE id = ?
        """,
        [(now, question_id) for question_id in stale_pending_ids],
    )
    conn.commit()


def build_question_generation_messages(
    *,
    domain: str,
    attribute_count: int,
    recent_tags: list[str],
    feedback_count: int,
) -> list[dict[str, str]]:
    """Build a sanitized prompt for dynamic Teach question generation."""
    instructions = (
        "You create one helpful onboarding question for a privacy-first identity engine. "
        "Only use the metadata provided. Do not mention models, privacy policy, or system internals. "
        "Return compact JSON with keys: question, intent_key."
    )
    payload = {
        "domain": domain,
        "attribute_count": attribute_count,
        "recent_upload_tags": recent_tags[:5],
        "prior_feedback_count": feedback_count,
    }
    return [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": (
                "Create one question that would teach the engine something useful without being annoying.\n"
                f"Metadata: {json.dumps(payload, sort_keys=True)}"
            ),
        },
    ]


def _parse_generated_question(raw: str, domain: str) -> tuple[str, str] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    question = str(payload.get("question", "")).strip()
    intent_key = str(payload.get("intent_key", "")).strip()
    if not question:
        return None
    if not intent_key:
        intent_key = f"{domain}_{_slug(question)}"
    return question, intent_key


def _dynamic_generation_available(provider_config: ProviderConfig) -> bool:
    """Return True when dynamic follow-up generation can run without side effects."""
    if getattr(provider_config, "is_local", False):
        try:
            resolve_local_provider_config(provider_config)
        except ConfigurationError:
            return False
        return True
    return bool(getattr(provider_config, "api_key", None))


def _insert_question(
    conn,
    *,
    prompt: str,
    domain: str | None,
    intent_key: str,
    source: str,
    priority: float,
    onboarding_stage: str = "teaching",
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority,
            onboarding_stage, asked_count, answer_count, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, 0, 0, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            prompt,
            domain,
            intent_key,
            source,
            priority,
            onboarding_stage,
            now,
            now,
        ),
    )


def _synthesis_prompt(theme_label: str, domains: list[str]) -> str:
    domain_text = ", ".join(domains)
    return (
        f"We noticed a {theme_label} thread across your {domain_text}. "
        "Does that resonate with you right now?"
    )


def _contradiction_prompt(
    *,
    attribute_a_value: str,
    attribute_b_value: str,
) -> str:
    return (
        "You've described yourself in two different ways: "
        f"\"{attribute_a_value}\" and \"{attribute_b_value}\". "
        "Which feels more accurate now?"
    )


def _confidence_decay_prompt(label: str, value: str) -> str:
    return (
        f"It's been a while since you confirmed '{label}'. "
        f"Is this still true: \"{value}\"?"
    )


def _stage_temporal_questions(
    conn,
    *,
    seen_intents: set[str],
    seen_prompts: set[str],
) -> int:
    events = refresh_temporal_intelligence(conn)
    inserted = 0

    for event in events:
        if event.event_type != "confidence_decay":
            continue
        if not event.attribute_ids:
            continue
        attribute_id = event.attribute_ids[0]
        attr_row = conn.execute(
            "SELECT label, value FROM attributes WHERE id = ?",
            (attribute_id,),
        ).fetchone()
        if attr_row is None:
            continue
        label = str(attr_row[0])
        value = str(attr_row[1])
        intent_key = f"temporal_decay_{event.id}"
        prompt = _confidence_decay_prompt(label, value)
        normalized_prompt = _normalize_prompt(prompt)
        if intent_key in seen_intents or normalized_prompt in seen_prompts:
            continue
        _insert_question(
            conn,
            prompt=prompt,
            domain=event.domain,
            intent_key=intent_key,
            source="temporal",
            priority=12.0,
        )
        seen_intents.add(intent_key)
        seen_prompts.add(normalized_prompt)
        inserted += 1
        if inserted >= 2:
            break

    return inserted


def _stage_cross_domain_questions(
    conn,
    *,
    seen_intents: set[str],
    seen_prompts: set[str],
) -> int:
    refresh_result = refresh_cross_domain_intelligence(conn)
    inserted = 0

    for item in refresh_result.syntheses[:2]:
        prompt = _synthesis_prompt(item.theme_label, item.domains_involved)
        intent_key = f"synthesis_review_{item.id}"
        normalized_prompt = _normalize_prompt(prompt)
        if intent_key in seen_intents or normalized_prompt in seen_prompts:
            continue
        _insert_question(
            conn,
            prompt=prompt,
            domain=item.domains_involved[0] if item.domains_involved else None,
            intent_key=intent_key,
            source="synthesis",
            priority=14.0 + float(item.strength),
        )
        seen_intents.add(intent_key)
        seen_prompts.add(normalized_prompt)
        inserted += 1

    for item in refresh_result.contradictions[:2]:
        prompt = _contradiction_prompt(
            attribute_a_value=item.attribute_a_value,
            attribute_b_value=item.attribute_b_value,
        )
        intent_key = f"contradiction_review_{item.id}"
        normalized_prompt = _normalize_prompt(prompt)
        if intent_key in seen_intents or normalized_prompt in seen_prompts:
            continue
        _insert_question(
            conn,
            prompt=prompt,
            domain=item.attribute_a_domain,
            intent_key=intent_key,
            source="contradiction",
            priority=13.0 + float(item.confidence),
        )
        seen_intents.add(intent_key)
        seen_prompts.add(normalized_prompt)
        inserted += 1

    return inserted


def ensure_question_queue(
    conn,
    provider_config: ProviderConfig,
    *,
    limit: int = QUESTION_LIMIT,
) -> None:
    """Ensure there are enough pending Teach questions for under-covered domains."""
    counts = _domain_attribute_counts(conn)
    feedback_counts = _feedback_counts(conn)
    seen_intents = _seen_intents(conn)
    seen_prompts = _seen_prompts(conn)
    tags = _artifact_tags(conn)
    _stage_cross_domain_questions(
        conn,
        seen_intents=seen_intents,
        seen_prompts=seen_prompts,
    )
    _stage_temporal_questions(
        conn,
        seen_intents=seen_intents,
        seen_prompts=seen_prompts,
    )

    ranked_domains = sorted(
        counts.items(),
        key=lambda item: (item[1], item[0]),
    )
    catalog_by_domain = {domain["name"]: domain["questions"] for domain in DOMAINS}

    pending_count = conn.execute(
        "SELECT COUNT(*) FROM teach_questions WHERE status = 'pending'"
    ).fetchone()
    current_pending = int(pending_count[0]) if pending_count is not None else 0
    generation_available: bool | None = None

    for domain, attribute_count in ranked_domains:
        if current_pending >= limit:
            break
        questions = catalog_by_domain.get(domain, [])
        feedback_count = feedback_counts.get(domain, 0)

        chosen_catalog = None
        for question in questions:
            intent_key = f"{domain}_{_slug(question)}"
            normalized_prompt = _normalize_prompt(question)
            if intent_key in seen_intents or normalized_prompt in seen_prompts:
                continue
            chosen_catalog = (question, intent_key)
            break
        if chosen_catalog is not None:
            prompt, intent_key = chosen_catalog
            _insert_question(
                conn,
                prompt=prompt,
                domain=domain,
                intent_key=intent_key,
                source="catalog",
                priority=max(1.0, 10.0 - attribute_count - feedback_count),
            )
            seen_intents.add(intent_key)
            seen_prompts.add(_normalize_prompt(prompt))
            current_pending += 1

        if current_pending >= limit:
            break

        generated_intent = f"{domain}_generated_follow_up"
        if generated_intent in seen_intents:
            continue
        if generation_available is None:
            generation_available = _dynamic_generation_available(provider_config)
        if not generation_available:
            continue

        messages = build_question_generation_messages(
            domain=domain,
            attribute_count=attribute_count,
            recent_tags=tags,
            feedback_count=feedback_count,
        )
        try:
            result = PrivacyBroker(provider_config).generate_grounded_response(
                messages,
                attributes=[],
                task_type="teach_question_generation",
                retrieval_mode="teach",
                contains_local_only_context=False,
                domains_used=[domain],
            )
        except Exception:
            continue
        if not isinstance(result.content, str):
            continue
        parsed = _parse_generated_question(result.content, domain)
        if parsed is None:
            continue
        prompt, intent_key = parsed
        if intent_key in seen_intents or _normalize_prompt(prompt) in seen_prompts:
            continue
        _insert_question(
            conn,
            prompt=prompt,
            domain=domain,
            intent_key=intent_key,
            source="generated",
            priority=max(0.5, 9.0 - attribute_count - feedback_count),
        )
        seen_intents.add(intent_key)
        seen_prompts.add(_normalize_prompt(prompt))
        current_pending += 1

    conn.commit()


def get_next_questions(conn, provider_config: ProviderConfig, *, limit: int = QUESTION_LIMIT) -> list[TeachQuestion]:
    """Return the next pending Teach questions after refreshing the queue."""
    _prune_stale_pending_questions(conn)
    ensure_question_queue(conn, provider_config, limit=max(limit, QUESTION_LIMIT))
    now = _now()
    rows = conn.execute(
        """
        SELECT id, prompt, domain, intent_key, source, status, priority, onboarding_stage
        FROM teach_questions
        WHERE status = 'pending'
        ORDER BY priority DESC, updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.executemany(
        """
        UPDATE teach_questions
        SET asked_count = asked_count + 1,
            last_presented_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        [(now, now, str(row[0])) for row in rows],
    )
    conn.commit()
    return [
        TeachQuestion(
            id=str(row[0]),
            prompt=str(row[1]),
            domain=str(row[2]) if row[2] else None,
            intent_key=str(row[3]),
            source=str(row[4]),
            status=str(row[5]),
            priority=float(row[6]),
            onboarding_stage=str(row[7]),
        )
        for row in rows
    ]


def get_question(conn, question_id: str) -> TeachQuestion | None:
    """Return one stored Teach question."""
    row = conn.execute(
        """
        SELECT id, prompt, domain, intent_key, source, status, priority, onboarding_stage
        FROM teach_questions
        WHERE id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return None
    return TeachQuestion(
        id=str(row[0]),
        prompt=str(row[1]),
        domain=str(row[2]) if row[2] else None,
        intent_key=str(row[3]),
        source=str(row[4]),
        status=str(row[5]),
        priority=float(row[6]),
        onboarding_stage=str(row[7]),
    )


def mark_question_answered(conn, question_id: str) -> None:
    """Mark a Teach question as answered."""
    now = _now()
    conn.execute(
        """
        UPDATE teach_questions
        SET status = 'answered',
            answer_count = answer_count + 1,
            updated_at = ?
        WHERE id = ?
        """,
        (now, question_id),
    )
    conn.commit()


def record_question_feedback(conn, question_id: str, feedback: str) -> None:
    """Persist feedback and dismiss the current question from the active queue."""
    now = _now()
    conn.execute(
        """
        INSERT INTO teach_question_feedback (id, question_id, feedback, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), question_id, feedback, now),
    )
    conn.execute(
        """
        UPDATE teach_questions
        SET status = 'dismissed',
            updated_at = ?
        WHERE id = ?
        """,
        (now, question_id),
    )
    conn.commit()
