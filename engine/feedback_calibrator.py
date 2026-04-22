"""Deterministic query-feedback aggregation for retrieval calibration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import uuid

_PATTERN_SIGNS = {
    "helpful": 1.0,
    "missed_context": -1.0,
    "wrong_focus": -1.0,
    "ungrounded": -1.0,
}

_PATTERN_WEIGHTS = {
    "helpful": 0.18,
    "missed_context": 0.24,
    "wrong_focus": 0.14,
    "ungrounded": 0.20,
}

_DOMAIN_DELTA_CAP = 0.15
_RECENT_MISSED_CONTEXT_RATE = 0.6


@dataclass(frozen=True)
class RecentFeedbackGap:
    """Recent missed-context concentration for one domain/profile pair."""

    domain: str
    observation_count: int
    missed_context_rate: float


def _parse_json_list(raw: object) -> list[str]:
    if raw in {None, ""}:
        return []
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    values: list[str] = []
    for item in payload:
        text = str(item or "").strip()
        if text:
            values.append(text)
    return values


def _parse_timestamp(raw: object) -> datetime | None:
    if raw in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _domains_for_feedback(domains_json: object, domain_hints_json: object) -> list[str]:
    explicit = _parse_json_list(domains_json)
    hinted = _parse_json_list(domain_hints_json)
    domains = explicit or hinted
    return sorted(set(domains))


def _group_feedback_rows(conn) -> tuple[dict[tuple[str, str], dict[str, int]], dict[str, float], int]:
    rows = conn.execute(
        """
        SELECT feedback, source_profile, domains_json, domain_hints_json
        FROM query_feedback
        """
    ).fetchall()

    totals_by_pattern = {pattern: 0 for pattern in _PATTERN_SIGNS}
    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"observations": 0, **{pattern: 0 for pattern in _PATTERN_SIGNS}}
    )

    for feedback, source_profile, domains_json, domain_hints_json in rows:
        pattern = str(feedback or "").strip()
        if pattern not in _PATTERN_SIGNS:
            continue
        domains = _domains_for_feedback(domains_json, domain_hints_json)
        if not domains:
            continue

        totals_by_pattern[pattern] += 1
        profile = str(source_profile or "general").strip() or "general"
        for domain in domains:
            bucket = grouped[(domain, profile)]
            bucket["observations"] += 1
            bucket[pattern] += 1

    total_rows = sum(totals_by_pattern.values())
    if total_rows <= 0:
        baseline_rates = {pattern: 0.0 for pattern in _PATTERN_SIGNS}
    else:
        baseline_rates = {
            pattern: totals_by_pattern[pattern] / total_rows for pattern in _PATTERN_SIGNS
        }
    return grouped, baseline_rates, total_rows


def recompute_retrieval_calibration(
    conn,
    *,
    min_observations: int = 4,
) -> int:
    """Rebuild `retrieval_calibration` from accumulated query feedback."""
    grouped, baseline_rates, _total_rows = _group_feedback_rows(conn)
    computed_at = datetime.now(UTC).isoformat()

    conn.execute("DELETE FROM retrieval_calibration")
    inserted = 0

    for (domain, source_profile), counts in grouped.items():
        observations = int(counts["observations"])
        if observations < min_observations:
            continue

        for pattern, sign in _PATTERN_SIGNS.items():
            group_rate = counts[pattern] / observations
            baseline_rate = baseline_rates[pattern]
            delta = (group_rate - baseline_rate) * _PATTERN_WEIGHTS[pattern] * sign
            delta = max(-_DOMAIN_DELTA_CAP, min(_DOMAIN_DELTA_CAP, delta))

            conn.execute(
                """
                INSERT INTO retrieval_calibration (
                    id,
                    domain,
                    source_profile,
                    feedback_pattern,
                    score_delta,
                    observation_count,
                    last_computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    domain,
                    source_profile,
                    pattern,
                    round(delta, 4),
                    observations,
                    computed_at,
                ),
            )
            inserted += 1

    conn.commit()
    return inserted


def maybe_run_feedback_calibration(
    conn,
    *,
    trigger_every: int = 10,
    min_observations: int = 4,
) -> bool:
    """Recompute calibration after each batch of new query feedback rows."""
    total_feedback = int(
        conn.execute("SELECT COUNT(*) FROM query_feedback").fetchone()[0] or 0
    )
    if total_feedback < trigger_every:
        return False

    latest_computed_at = conn.execute(
        "SELECT MAX(last_computed_at) FROM retrieval_calibration"
    ).fetchone()[0]
    if latest_computed_at not in {None, ""}:
        new_feedback = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM query_feedback
                WHERE created_at > ?
                """,
                (latest_computed_at,),
            ).fetchone()[0]
            or 0
        )
        if new_feedback < trigger_every:
            return False

    recompute_retrieval_calibration(conn, min_observations=min_observations)
    return True


def load_retrieval_calibration(
    conn,
    *,
    source_profile: str | None = None,
) -> dict[str, float]:
    """Return bounded domain-level retrieval deltas for one source profile."""
    rows = conn.execute(
        """
        SELECT domain, source_profile, score_delta
        FROM retrieval_calibration
        """
    ).fetchall()

    totals: dict[str, float] = defaultdict(float)
    for domain, row_source_profile, score_delta in rows:
        profile = str(row_source_profile or "general").strip() or "general"
        if source_profile is not None and profile != source_profile:
            continue
        totals[str(domain)] += float(score_delta or 0.0)

    return {
        domain: round(max(-_DOMAIN_DELTA_CAP, min(_DOMAIN_DELTA_CAP, delta)), 4)
        for domain, delta in totals.items()
    }


def recent_feedback_gap(
    conn,
    *,
    domains: list[str],
    source_profile: str,
    lookback_days: int = 90,
    min_observations: int = 3,
) -> RecentFeedbackGap | None:
    """Return recent missed-context concentration for the requested domains."""
    target_domains = sorted(set(str(domain).strip() for domain in domains if str(domain).strip()))
    if not target_domains:
        return None

    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    rows = conn.execute(
        """
        SELECT feedback, domains_json, domain_hints_json, created_at
        FROM query_feedback
        WHERE source_profile = ?
        ORDER BY created_at DESC
        """,
        (source_profile,),
    ).fetchall()

    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"observations": 0, "missed": 0})
    for feedback, domains_json, domain_hints_json, created_at in rows:
        created_at_dt = _parse_timestamp(created_at)
        if created_at_dt is None or created_at_dt < cutoff:
            continue

        present_domains = _domains_for_feedback(domains_json, domain_hints_json)
        if not present_domains:
            continue

        for domain in present_domains:
            if domain not in target_domains:
                continue
            bucket = counts[domain]
            bucket["observations"] += 1
            if str(feedback or "") == "missed_context":
                bucket["missed"] += 1

    best_gap: RecentFeedbackGap | None = None
    for domain, values in counts.items():
        observations = int(values["observations"])
        if observations < min_observations:
            continue
        missed_rate = values["missed"] / observations
        if missed_rate < _RECENT_MISSED_CONTEXT_RATE:
            continue
        candidate = RecentFeedbackGap(
            domain=domain,
            observation_count=observations,
            missed_context_rate=missed_rate,
        )
        if best_gap is None or candidate.missed_context_rate > best_gap.missed_context_rate:
            best_gap = candidate
    return best_gap


def build_recent_feedback_gap_note(
    conn,
    *,
    domains: list[str],
    source_profile: str,
) -> str | None:
    """Return a short low-confidence note when recent feedback shows missed context."""
    gap = recent_feedback_gap(
        conn,
        domains=domains,
        source_profile=source_profile,
    )
    if gap is None:
        return None
    return (
        f"Recent feedback on {gap.domain} answers has frequently flagged missed context "
        f"({gap.observation_count} recent ratings), so similar responses may still be under-grounded."
    )
