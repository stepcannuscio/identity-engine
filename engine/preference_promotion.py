"""Promote repeated preference signals into inferred attributes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
import uuid

from config.settings import EVOLVING, INFERRED, LOCAL_ONLY
from db.inference_evidence import InferenceEvidenceInput, record_inference_evidence_batch
from db.preference_signals import PreferenceSignalRecord, list_preference_signals

_POSITIVE_SIGNALS = {"like", "accept", "prefer"}
_NEGATIVE_SIGNALS = {"dislike", "reject", "avoid"}

_MIN_SIGNAL_COUNT = 3
_MIN_POSITIVE_COUNT = 3
_MIN_NET_SCORE = 6
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class PreferenceAggregate:
    """Deterministic grouped summary for one preference subject."""

    category: str
    subject: str
    observations: int
    positive_count: int
    negative_count: int
    positive_score: int
    negative_score: int
    net_score: int
    latest_at: str
    signals: tuple[PreferenceSignalRecord, ...]


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Promotion classification for one preference aggregate."""

    category: str
    subject: str
    state: str
    should_promote: bool
    reason: str
    domain: str
    label: str
    value: str
    confidence: float
    evidence_summary: str


@dataclass(frozen=True, slots=True)
class PreferencePromotionResult:
    """Stored outcome for one promotion attempt."""

    category: str
    subject: str
    state: str
    action: str
    reason: str
    domain: str
    label: str
    attribute_id: str | None
    confidence: float | None
    observations: int
    positive_count: int
    negative_count: int
    net_score: int


@dataclass(frozen=True, slots=True)
class _CurrentAttribute:
    id: str
    value: str
    confidence: float
    source: str
    status: str
    routing: str


@dataclass(frozen=True, slots=True)
class _HistoricalMatch:
    id: str
    status: str
    source: str


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _slugify(value: str) -> str:
    lowered = value.strip().lower().replace("'", "")
    slug = _SLUG_RE.sub("_", lowered).strip("_")
    return slug or "preference"


def _humanize(value: str) -> str:
    return value.strip().replace("_", " ")


def _domain_for_category(category: str) -> str:
    normalized = category.strip().lower()
    if any(token in normalized for token in ("writing", "voice", "tone", "communication")):
        return "voice"
    return "patterns"


def _label_for(category: str, subject: str) -> str:
    return f"preference_{_slugify(category)}_{_slugify(subject)}"


def _value_for(subject: str) -> str:
    return f"I prefer {_humanize(subject)}."


def _summary_for(aggregate: PreferenceAggregate) -> str:
    return (
        f"Based on {aggregate.positive_count} positive and "
        f"{aggregate.negative_count} negative signals for {_humanize(aggregate.subject)}."
    )


def _confidence_for(aggregate: PreferenceAggregate) -> float:
    base = 0.55 + (min(aggregate.net_score, 10) * 0.03) + (min(aggregate.positive_count, 5) * 0.02)
    if aggregate.negative_count == 0:
        base += 0.03
    return round(min(base, 0.95), 2)


def _write_attribute_history(
    conn,
    *,
    attribute_id: str,
    previous_value: str,
    previous_confidence: float,
    reason: str,
    changed_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO attribute_history (
            id,
            attribute_id,
            previous_value,
            previous_confidence,
            reason,
            changed_at,
            changed_by
        )
        VALUES (?, ?, ?, ?, ?, ?, 'inferred')
        """,
        (
            str(uuid.uuid4()),
            attribute_id,
            previous_value,
            previous_confidence,
            reason,
            changed_at,
        ),
    )


def _get_domain_id(conn, domain: str) -> str:
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (domain,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown domain '{domain}'.")
    return str(row[0])


def _get_current_attribute(conn, domain: str, label: str) -> _CurrentAttribute | None:
    row = conn.execute(
        """
        SELECT
            a.id,
            a.value,
            a.confidence,
            a.source,
            a.status,
            a.routing
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE d.name = ? AND a.label = ? AND a.status IN ('active', 'confirmed')
        LIMIT 1
        """,
        (domain, label),
    ).fetchone()
    if row is None:
        return None
    return _CurrentAttribute(
        id=str(row[0]),
        value=str(row[1]),
        confidence=float(row[2]),
        source=str(row[3]),
        status=str(row[4]),
        routing=str(row[5]),
    )


def _get_current_duplicate_value(conn, domain: str, value: str) -> _CurrentAttribute | None:
    row = conn.execute(
        """
        SELECT
            a.id,
            a.value,
            a.confidence,
            a.source,
            a.status,
            a.routing
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE d.name = ? AND a.value = ? AND a.status IN ('active', 'confirmed')
        LIMIT 1
        """,
        (domain, value),
    ).fetchone()
    if row is None:
        return None
    return _CurrentAttribute(
        id=str(row[0]),
        value=str(row[1]),
        confidence=float(row[2]),
        source=str(row[3]),
        status=str(row[4]),
        routing=str(row[5]),
    )


def _get_latest_historical_match(
    conn,
    domain: str,
    label: str,
    value: str,
) -> _HistoricalMatch | None:
    row = conn.execute(
        """
        SELECT
            a.id,
            a.status,
            a.source
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE d.name = ? AND (a.label = ? OR a.value = ?)
        ORDER BY a.updated_at DESC, a.created_at DESC, a.id DESC
        LIMIT 1
        """,
        (domain, label, value),
    ).fetchone()
    if row is None:
        return None
    return _HistoricalMatch(
        id=str(row[0]),
        status=str(row[1]),
        source=str(row[2]),
    )


def _create_promoted_attribute(
    conn,
    *,
    decision: PromotionDecision,
) -> str:
    now = _utcnow()
    attribute_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO attributes (
            id,
            domain_id,
            label,
            value,
            elaboration,
            mutability,
            source,
            confidence,
            routing,
            status,
            created_at,
            updated_at,
            last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            attribute_id,
            _get_domain_id(conn, decision.domain),
            decision.label,
            decision.value,
            "Inferred from repeated local preference signals.",
            EVOLVING,
            INFERRED,
            decision.confidence,
            LOCAL_ONLY,
            now,
            now,
            None,
        ),
    )
    return attribute_id


def _refresh_promoted_attribute(
    conn,
    current: _CurrentAttribute,
    *,
    target_confidence: float,
) -> tuple[float, bool]:
    next_confidence = max(current.confidence, target_confidence)
    if current.status == "confirmed":
        next_confidence = max(next_confidence, min(current.confidence + 0.05, 1.0))
    next_confidence = round(min(next_confidence, 1.0), 2)
    routing_changed = current.routing != LOCAL_ONLY
    changed = (next_confidence != current.confidence) or routing_changed
    if not changed:
        return current.confidence, False

    now = _utcnow()
    _write_attribute_history(
        conn,
        attribute_id=current.id,
        previous_value=current.value,
        previous_confidence=current.confidence,
        reason="preference promotion refresh",
        changed_at=now,
    )
    conn.execute(
        """
        UPDATE attributes
        SET confidence = ?, routing = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            next_confidence,
            LOCAL_ONLY,
            now,
            current.id,
        ),
    )
    return next_confidence, True


def _attach_preference_evidence(
    conn,
    attribute_id: str,
    aggregate: PreferenceAggregate,
    *,
    summary_text: str,
) -> bool:
    existing_refs = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT source_ref
            FROM inference_evidence
            WHERE attribute_id = ? AND source_type = 'preference_signal' AND source_ref IS NOT NULL
            """,
            (attribute_id,),
        ).fetchall()
        if row[0] is not None
    }
    evidence_items = [
        InferenceEvidenceInput(
            source_type="preference_signal",
            source_ref=signal.id,
            supporting_text=summary_text,
            weight=round(signal.strength / 5, 2),
        )
        for signal in aggregate.signals
        if signal.id not in existing_refs
    ]
    if not evidence_items:
        return False
    record_inference_evidence_batch(conn, attribute_id, evidence_items)
    return True


def aggregate_signals(
    conn,
    *,
    category: str | None = None,
    subject: str | None = None,
) -> list[PreferenceAggregate]:
    """Group stored preference signals into deterministic aggregates."""
    grouped: dict[tuple[str, str], list[PreferenceSignalRecord]] = {}
    for signal in list_preference_signals(conn, category=category, subject=subject):
        grouped.setdefault((signal.category, signal.subject), []).append(signal)

    aggregates: list[PreferenceAggregate] = []
    for (group_category, group_subject), signals in grouped.items():
        ordered_signals = tuple(sorted(signals, key=lambda item: (item.created_at, item.id)))
        positive_count = sum(1 for signal in ordered_signals if signal.signal in _POSITIVE_SIGNALS)
        negative_count = sum(1 for signal in ordered_signals if signal.signal in _NEGATIVE_SIGNALS)
        positive_score = sum(
            signal.strength for signal in ordered_signals if signal.signal in _POSITIVE_SIGNALS
        )
        negative_score = sum(
            signal.strength for signal in ordered_signals if signal.signal in _NEGATIVE_SIGNALS
        )
        aggregates.append(
            PreferenceAggregate(
                category=group_category,
                subject=group_subject,
                observations=len(ordered_signals),
                positive_count=positive_count,
                negative_count=negative_count,
                positive_score=positive_score,
                negative_score=negative_score,
                net_score=positive_score - negative_score,
                latest_at=max(signal.created_at for signal in ordered_signals),
                signals=ordered_signals,
            )
        )

    aggregates.sort(key=lambda item: (item.category, item.subject))
    return aggregates


def evaluate_promotion(aggregate: PreferenceAggregate) -> PromotionDecision:
    """Classify one aggregate as emerging, stable, or conflicting."""
    domain = _domain_for_category(aggregate.category)
    label = _label_for(aggregate.category, aggregate.subject)
    value = _value_for(aggregate.subject)

    if (
        aggregate.observations < _MIN_SIGNAL_COUNT
        or aggregate.positive_count < _MIN_POSITIVE_COUNT
    ):
        return PromotionDecision(
            category=aggregate.category,
            subject=aggregate.subject,
            state="emerging",
            should_promote=False,
            reason="not enough repeated positive signals yet",
            domain=domain,
            label=label,
            value=value,
            confidence=_confidence_for(aggregate),
            evidence_summary=_summary_for(aggregate),
        )

    if aggregate.net_score <= 0 or aggregate.negative_score >= aggregate.positive_score:
        return PromotionDecision(
            category=aggregate.category,
            subject=aggregate.subject,
            state="conflicting",
            should_promote=False,
            reason="conflicting signals outweigh the positive pattern",
            domain=domain,
            label=label,
            value=value,
            confidence=_confidence_for(aggregate),
            evidence_summary=_summary_for(aggregate),
        )

    if aggregate.negative_count > 0 and (aggregate.negative_score * 2) > aggregate.positive_score:
        return PromotionDecision(
            category=aggregate.category,
            subject=aggregate.subject,
            state="conflicting",
            should_promote=False,
            reason="signal pattern is too mixed to promote safely",
            domain=domain,
            label=label,
            value=value,
            confidence=_confidence_for(aggregate),
            evidence_summary=_summary_for(aggregate),
        )

    if aggregate.net_score >= _MIN_NET_SCORE:
        return PromotionDecision(
            category=aggregate.category,
            subject=aggregate.subject,
            state="stable",
            should_promote=True,
            reason="stable repeated preference pattern detected",
            domain=domain,
            label=label,
            value=value,
            confidence=_confidence_for(aggregate),
            evidence_summary=_summary_for(aggregate),
        )

    return PromotionDecision(
        category=aggregate.category,
        subject=aggregate.subject,
        state="emerging",
        should_promote=False,
        reason="preference pattern is positive but not strong enough yet",
        domain=domain,
        label=label,
        value=value,
        confidence=_confidence_for(aggregate),
        evidence_summary=_summary_for(aggregate),
    )


def promote_preference(
    conn,
    aggregate: PreferenceAggregate,
    decision: PromotionDecision | None = None,
) -> PreferencePromotionResult:
    """Create or refresh one inferred preference attribute when eligible."""
    resolved = decision or evaluate_promotion(aggregate)

    if not resolved.should_promote:
        return PreferencePromotionResult(
            category=aggregate.category,
            subject=aggregate.subject,
            state=resolved.state,
            action="noop",
            reason=resolved.reason,
            domain=resolved.domain,
            label=resolved.label,
            attribute_id=None,
            confidence=None,
            observations=aggregate.observations,
            positive_count=aggregate.positive_count,
            negative_count=aggregate.negative_count,
            net_score=aggregate.net_score,
        )

    current = _get_current_attribute(conn, resolved.domain, resolved.label)
    if current is not None:
        if current.source != INFERRED:
            return PreferencePromotionResult(
                category=aggregate.category,
                subject=aggregate.subject,
                state=resolved.state,
                action="blocked_existing",
                reason="matching current attribute is user-owned and will not be overwritten",
                domain=resolved.domain,
                label=resolved.label,
                attribute_id=current.id,
                confidence=current.confidence,
                observations=aggregate.observations,
                positive_count=aggregate.positive_count,
                negative_count=aggregate.negative_count,
                net_score=aggregate.net_score,
            )

        confidence, attribute_changed = _refresh_promoted_attribute(
            conn,
            current,
            target_confidence=resolved.confidence,
        )
        evidence_added = _attach_preference_evidence(
            conn,
            current.id,
            aggregate,
            summary_text=resolved.evidence_summary,
        )
        if attribute_changed and not evidence_added:
            conn.commit()
        action = "updated" if (attribute_changed or evidence_added) else "noop"
        return PreferencePromotionResult(
            category=aggregate.category,
            subject=aggregate.subject,
            state=resolved.state,
            action=action,
            reason=resolved.reason,
            domain=resolved.domain,
            label=resolved.label,
            attribute_id=current.id,
            confidence=confidence,
            observations=aggregate.observations,
            positive_count=aggregate.positive_count,
            negative_count=aggregate.negative_count,
            net_score=aggregate.net_score,
        )

    duplicate = _get_current_duplicate_value(conn, resolved.domain, resolved.value)
    if duplicate is not None:
        return PreferencePromotionResult(
            category=aggregate.category,
            subject=aggregate.subject,
            state=resolved.state,
            action="blocked_existing",
            reason="matching current preference already exists",
            domain=resolved.domain,
            label=resolved.label,
            attribute_id=duplicate.id,
            confidence=duplicate.confidence,
            observations=aggregate.observations,
            positive_count=aggregate.positive_count,
            negative_count=aggregate.negative_count,
            net_score=aggregate.net_score,
        )

    historical = _get_latest_historical_match(conn, resolved.domain, resolved.label, resolved.value)
    if historical is not None and historical.status == "rejected":
        return PreferencePromotionResult(
            category=aggregate.category,
            subject=aggregate.subject,
            state=resolved.state,
            action="blocked_rejected",
            reason="latest matching attribute was rejected by the user",
            domain=resolved.domain,
            label=resolved.label,
            attribute_id=historical.id,
            confidence=None,
            observations=aggregate.observations,
            positive_count=aggregate.positive_count,
            negative_count=aggregate.negative_count,
            net_score=aggregate.net_score,
        )

    attribute_id = _create_promoted_attribute(conn, decision=resolved)
    evidence_added = _attach_preference_evidence(
        conn,
        attribute_id,
        aggregate,
        summary_text=resolved.evidence_summary,
    )
    if not evidence_added:
        conn.commit()

    return PreferencePromotionResult(
        category=aggregate.category,
        subject=aggregate.subject,
        state=resolved.state,
        action="created",
        reason=resolved.reason,
        domain=resolved.domain,
        label=resolved.label,
        attribute_id=attribute_id,
        confidence=resolved.confidence,
        observations=aggregate.observations,
        positive_count=aggregate.positive_count,
        negative_count=aggregate.negative_count,
        net_score=aggregate.net_score,
    )


def run_preference_promotion(
    conn,
    *,
    category: str | None = None,
    subject: str | None = None,
) -> list[PreferencePromotionResult]:
    """Run deterministic preference promotion for one optional filter scope."""
    results: list[PreferencePromotionResult] = []
    for aggregate in aggregate_signals(conn, category=category, subject=subject):
        decision = evaluate_promotion(aggregate)
        results.append(promote_preference(conn, aggregate, decision))
    return results
