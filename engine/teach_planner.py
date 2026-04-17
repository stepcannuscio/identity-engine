"""Teach question planning and persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
import uuid

from config.llm_router import ProviderConfig
from engine.interview_catalog import DOMAINS
from engine.privacy_broker import PrivacyBroker

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


def _pending_intents(conn) -> set[str]:
    rows = conn.execute(
        "SELECT intent_key FROM teach_questions WHERE status = 'pending'"
    ).fetchall()
    return {str(row[0]) for row in rows}


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


def ensure_question_queue(
    conn,
    provider_config: ProviderConfig,
    *,
    limit: int = QUESTION_LIMIT,
) -> None:
    """Ensure there are enough pending Teach questions for under-covered domains."""
    counts = _domain_attribute_counts(conn)
    feedback_counts = _feedback_counts(conn)
    pending_intents = _pending_intents(conn)
    tags = _artifact_tags(conn)

    ranked_domains = sorted(
        counts.items(),
        key=lambda item: (item[1], item[0]),
    )
    catalog_by_domain = {domain["name"]: domain["questions"] for domain in DOMAINS}

    pending_count = conn.execute(
        "SELECT COUNT(*) FROM teach_questions WHERE status = 'pending'"
    ).fetchone()
    current_pending = int(pending_count[0]) if pending_count is not None else 0

    for domain, attribute_count in ranked_domains:
        if current_pending >= limit:
            break
        questions = catalog_by_domain.get(domain, [])
        feedback_count = feedback_counts.get(domain, 0)

        chosen_catalog = None
        for question in questions:
            intent_key = f"{domain}_{_slug(question)}"
            if intent_key in pending_intents:
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
            pending_intents.add(intent_key)
            current_pending += 1

        if current_pending >= limit:
            break

        generated_intent = f"{domain}_generated_follow_up"
        if generated_intent in pending_intents:
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
        if intent_key in pending_intents:
            continue
        _insert_question(
            conn,
            prompt=prompt,
            domain=domain,
            intent_key=intent_key,
            source="generated",
            priority=max(0.5, 9.0 - attribute_count - feedback_count),
        )
        pending_intents.add(intent_key)
        current_pending += 1

    conn.commit()


def get_next_questions(conn, provider_config: ProviderConfig, *, limit: int = QUESTION_LIMIT) -> list[TeachQuestion]:
    """Return the next pending Teach questions after refreshing the queue."""
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
