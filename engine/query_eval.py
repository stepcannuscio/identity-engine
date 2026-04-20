"""Local evaluation harness for deterministic query usefulness tuning."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, cast
import uuid

from config.llm_router import ProviderConfig
from db.connection import get_plain_connection
from db.preference_signals import PreferenceSignalInput, record_preference_signal
from db.schema import create_tables, seed_domains
from engine.artifact_ingestion import ingest_artifact
from engine.query_engine import prepare_query
from engine.session import Session

CORPUS_DIR = Path(__file__).resolve().parent.parent / "evals" / "query_usefulness"


@dataclass(frozen=True)
class EvalCaseResult:
    """One case result plus subsystem pass/fail signals."""

    case_id: str
    passed: bool
    checks: dict[str, bool]
    actual: dict[str, object]
    expected: dict[str, object]


def _provider_config(backend: str) -> ProviderConfig:
    is_local = backend == "local"
    return ProviderConfig(
        provider="ollama" if is_local else "anthropic",
        api_key=None if is_local else "test-key",
        model="llama3.1:8b" if is_local else "claude-sonnet-4-6",
        is_local=is_local,
        arch="apple_silicon",
        ram_gb=36.0,
    )


def _load_corpus(version: str) -> dict[str, Any]:
    path = CORPUS_DIR / f"{version}.json"
    return cast(dict[str, Any], json.loads(path.read_text()))


def _domain_ids(conn) -> dict[str, str]:
    rows = conn.execute("SELECT id, name FROM domains").fetchall()
    return {str(name): str(domain_id) for domain_id, name in rows}


def _insert_attribute(conn, domains: dict[str, str], item: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
        """,
        (
            str(uuid.uuid4()),
            domains[str(item["domain"])],
            str(item["label"]),
            str(item["value"]),
            item.get("elaboration"),
            str(item.get("mutability", "evolving")),
            str(item.get("source", "reflection")),
            float(item.get("confidence", 0.8)),
            str(item.get("routing", "local_only")),
            str(item.get("status", "active")),
            "2026-04-15T12:00:00+00:00" if item.get("status") == "confirmed" else None,
        ),
    )


def _seed_case(conn, seed: dict[str, Any]) -> None:
    domains = _domain_ids(conn)
    for attribute in cast(list[dict[str, Any]], seed.get("attributes", [])):
        _insert_attribute(conn, domains, attribute)

    for signal in cast(list[dict[str, Any]], seed.get("preference_signals", [])):
        record_preference_signal(
            conn,
            PreferenceSignalInput(
                category=str(signal["category"]),
                subject=str(signal["subject"]),
                signal=str(signal["signal"]),
                strength=int(signal.get("strength", 3)),
            ),
        )

    for artifact in cast(list[dict[str, Any]], seed.get("artifacts", [])):
        ingest_artifact(
            conn,
            text=str(artifact["text"]),
            title=str(artifact["title"]),
            artifact_type=str(artifact.get("type", "note")),
            source=str(artifact.get("source", "eval")),
            domain=str(artifact.get("domain")) if artifact.get("domain") else None,
        )

    conn.commit()


def evaluate_case(case: dict[str, Any]) -> EvalCaseResult:
    """Run one corpus case through the deterministic query pipeline."""
    with get_plain_connection(":memory:") as conn:
        create_tables(conn)
        seed_domains(conn)
        _seed_case(conn, cast(dict[str, Any], case.get("seed", {})))
        context = prepare_query(
            str(case["query"]),
            Session(),
            conn,
            _provider_config(str(case.get("backend", "local"))),
        )

    top_source_type = (
        context.assembled_context.evidence_items[0].source_type
        if context.assembled_context.evidence_items
        else None
    )
    actual = {
        "query_type": context.query_type,
        "source_profile": context.source_profile,
        "confidence": context.coverage.confidence,
        "acquisition_status": context.acquisition.status,
        "top_source_type": top_source_type,
        "has_voice_profile": context.assembled_context.voice_profile is not None,
        "prompt_contains_voice_guidance": "Voice guidance:" in context.messages[0]["content"],
        "prompt_contains_exemplar_snippets": "Local exemplar snippets:" in context.messages[0]["content"],
        "would_block_external": (
            context.backend == "external"
            and any(attr.get("routing") == "local_only" for attr in context.attributes)
        ),
    }
    expected = cast(dict[str, Any], case["expected"])
    checks = {key: actual.get(key) == value for key, value in expected.items()}
    return EvalCaseResult(
        case_id=str(case["id"]),
        passed=all(checks.values()),
        checks=checks,
        actual=actual,
        expected=expected,
    )


def evaluate_corpus(version: str = "v1") -> list[EvalCaseResult]:
    """Run the full corpus and return per-case results."""
    corpus = _load_corpus(version)
    return [evaluate_case(case) for case in cast(list[dict[str, Any]], corpus["cases"])]


def main() -> int:
    """CLI entrypoint for `python -m engine.query_eval`."""
    results = evaluate_corpus()
    totals: dict[str, tuple[int, int]] = {}
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.case_id}")
        if not result.passed:
            print(f"  expected={result.expected}")
            print(f"  actual={result.actual}")
        for key, passed in result.checks.items():
            wins, total = totals.get(key, (0, 0))
            totals[key] = (wins + int(passed), total + 1)

    print("\nAggregate accuracy:")
    for key in sorted(totals):
        wins, total = totals[key]
        print(f"  {key}: {wins}/{total}")

    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
