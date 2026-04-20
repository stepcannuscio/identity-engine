"""Unit tests for artifact analysis worker and enqueue/status helpers."""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.artifact_ingestion import ingest_artifact
from engine.artifact_analysis import (
    _build_fallback_analysis,
    _call_provider,
    enqueue_artifact_analysis,
    get_artifact_analysis,
    run_analysis_for_worker,
)


@pytest.fixture
def conn():
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


@pytest.fixture
def artifact_id(conn):
    result = ingest_artifact(
        conn,
        text="Lasagna, tikka masala, and pasta bake are recipes I have made.",
        title="Dinner recipes",
        artifact_type="document",
        source="upload",
        domain="patterns",
    )
    return result.artifact_id


def test_enqueue_artifact_analysis_writes_queued_status(conn, artifact_id):
    analysis = enqueue_artifact_analysis(conn, artifact_id)

    assert analysis["status"] == "queued"
    assert analysis["queued_at"] is not None
    assert analysis["started_at"] is None
    assert analysis["completed_at"] is None


def test_enqueue_artifact_analysis_is_idempotent_when_already_queued(conn, artifact_id):
    first = enqueue_artifact_analysis(conn, artifact_id)
    second = enqueue_artifact_analysis(conn, artifact_id)

    assert second["status"] == "queued"
    assert second["queued_at"] == first["queued_at"]


def test_enqueue_artifact_analysis_raises_for_unknown_artifact(conn):
    with pytest.raises(ValueError, match="artifact not found"):
        enqueue_artifact_analysis(conn, "does-not-exist")


def _local_config():
    from config.llm_router import ProviderConfig

    return ProviderConfig(
        provider="ollama",
        api_key=None,
        model="llama3.1:8b",
        is_local=True,
        arch="apple_silicon",
        ram_gb=36.0,
    )


def test_run_analysis_for_worker_transitions_to_analyzed(conn, artifact_id):
    enqueue_artifact_analysis(conn, artifact_id)

    llm_config = _local_config()

    @contextmanager
    def _get_conn():
        yield conn

    with (
        patch("engine.setup_state.resolve_local_provider_config", return_value=llm_config),
        patch("server.db.get_db_connection", _get_conn),
        patch("engine.artifact_analysis.PrivacyBroker") as mock_broker_cls,
    ):
        mock_broker = MagicMock()
        mock_broker.extract_structured_attributes.return_value = SimpleNamespace(
            content='{"content_kind":"recipe_collection","summary":"A collection of dinner recipes.","descriptor_tokens":["recipe","dinner"],"candidate_attributes":[],"candidate_preferences":[]}'
        )
        mock_broker_cls.return_value = mock_broker

        run_analysis_for_worker(artifact_id, llm_config)

    analysis = get_artifact_analysis(conn, artifact_id)
    assert analysis is not None
    assert analysis["status"] == "analyzed"
    assert analysis["summary"] == "A collection of dinner recipes."
    assert analysis["completed_at"] is not None


def test_run_analysis_for_worker_transitions_to_fallback_on_timeout(conn, artifact_id):
    import requests

    enqueue_artifact_analysis(conn, artifact_id)
    llm_config = _local_config()

    @contextmanager
    def _get_conn():
        yield conn

    with (
        patch("engine.setup_state.resolve_local_provider_config", return_value=llm_config),
        patch("server.db.get_db_connection", _get_conn),
        patch("engine.artifact_analysis.PrivacyBroker") as mock_broker_cls,
    ):
        mock_broker = MagicMock()
        mock_broker.extract_structured_attributes.side_effect = requests.exceptions.ReadTimeout("timed out")
        mock_broker_cls.return_value = mock_broker

        run_analysis_for_worker(artifact_id, llm_config)

    analysis = get_artifact_analysis(conn, artifact_id)
    assert analysis is not None
    assert analysis["status"] == "fallback_analyzed"
    assert analysis["analysis_method"] == "heuristic_fallback"
    assert analysis["analysis_warning"] is not None


def test_run_analysis_for_worker_transitions_to_failed_on_unexpected_error(conn, artifact_id):
    enqueue_artifact_analysis(conn, artifact_id)
    llm_config = _local_config()

    @contextmanager
    def _get_conn():
        yield conn

    with (
        patch("engine.setup_state.resolve_local_provider_config", return_value=llm_config),
        patch("server.db.get_db_connection", _get_conn),
        patch("engine.artifact_analysis.PrivacyBroker") as mock_broker_cls,
    ):
        mock_broker = MagicMock()
        mock_broker.extract_structured_attributes.side_effect = RuntimeError("unexpected crash")
        mock_broker_cls.return_value = mock_broker

        run_analysis_for_worker(artifact_id, llm_config)

    analysis = get_artifact_analysis(conn, artifact_id)
    assert analysis is not None
    assert analysis["status"] == "failed"


def test_run_analysis_for_worker_skips_if_not_queued(conn, artifact_id):
    llm_config = _local_config()

    @contextmanager
    def _get_conn():
        yield conn

    with (
        patch("engine.setup_state.resolve_local_provider_config", return_value=llm_config),
        patch("server.db.get_db_connection", _get_conn),
        patch("engine.artifact_analysis.PrivacyBroker") as mock_broker_cls,
    ):
        run_analysis_for_worker(artifact_id, llm_config)
        assert not mock_broker_cls.called

    analysis = get_artifact_analysis(conn, artifact_id)
    assert analysis is None


def test_run_analysis_for_worker_fails_when_no_local_provider(conn, artifact_id):
    from config.llm_router import ConfigurationError

    enqueue_artifact_analysis(conn, artifact_id)
    llm_config = _local_config()

    @contextmanager
    def _get_conn():
        yield conn

    with (
        patch("engine.setup_state.resolve_local_provider_config", side_effect=ConfigurationError("no local")),
        patch("server.db.get_db_connection", _get_conn),
    ):
        run_analysis_for_worker(artifact_id, llm_config)

    analysis = get_artifact_analysis(conn, artifact_id)
    assert analysis is not None
    assert analysis["status"] == "failed"
