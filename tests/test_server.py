"""Tests for the FastAPI backend server."""

from __future__ import annotations

import json
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine.capture as capture_module
from config.llm_router import ProviderConfig
from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
from engine.prompt_builder import RoutingViolationError
from server.main import assert_safe_bind_ip, create_app


def _config() -> ProviderConfig:
    return ProviderConfig(
        provider="ollama",
        api_key=None,
        model="llama3.1:8b",
        is_local=True,
        arch="apple_silicon",
        ram_gb=36.0,
    )


def _domain_id(conn, domain: str) -> str:
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (domain,)).fetchone()
    assert row is not None
    return str(row[0])


def _insert_attribute(
    conn,
    domain: str,
    label: str,
    value: str,
    routing: str = "local_only",
    status: str = "active",
) -> str:
    now = "2026-04-08T12:00:00+00:00"
    attribute_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, 'stable', 'explicit', 0.8, ?, ?, ?, ?, ?)
        """,
        (
            attribute_id,
            _domain_id(conn, domain),
            label,
            value,
            None,
            routing,
            status,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return attribute_id


def _mock_capture_extraction(monkeypatch, attrs: list[dict]) -> None:
    monkeypatch.setattr(
        capture_module.PrivacyBroker,
        "extract_structured_attributes",
        lambda self, messages, task_type="capture_extraction": SimpleNamespace(
            content=json.dumps(attrs),
            metadata=SimpleNamespace(task_type=task_type),
        ),
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("server.main.get_bind_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(
        "server.main.ensure_tls_certs",
        lambda bind_ip: (Path("/tmp/key.pem"), Path("/tmp/cert.pem")),
    )
    monkeypatch.setattr("server.main.resolve_router", lambda: _config())
    monkeypatch.setattr("server.main.print_routing_report", lambda config: None)
    monkeypatch.setattr("server.main.ensure_ui_passphrase_exists", lambda: None)
    monkeypatch.setattr("server.main.shutdown_started_ollama", lambda: None)
    monkeypatch.setattr(
        "server.auth.get_ui_passphrase",
        lambda: "correct horse battery staple",
    )

    db_context = get_plain_connection(":memory:")
    conn = db_context.__enter__()
    create_tables(conn)
    seed_domains(conn)

    @contextmanager
    def _get_db_connection():
        yield conn

    monkeypatch.setattr("server.main.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.query.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.attributes.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.capture.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.session.get_db_connection", _get_db_connection)

    app = create_app()
    app.state.test_db = conn
    with TestClient(app) as test_client:
        yield test_client
    db_context.__exit__(None, None, None)


def _login_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/auth/login", json={"passphrase": "correct horse battery staple"})
    assert response.status_code == 200
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _app(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


def _db(client: TestClient):
    return _app(client).state.test_db


def test_health_returns_200_without_authentication(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_login_with_correct_passphrase_returns_token(client: TestClient):
    response = client.post("/auth/login", json={"passphrase": "correct horse battery staple"})
    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["expires_at"]


def test_login_with_wrong_passphrase_returns_401(client: TestClient):
    response = client.post("/auth/login", json={"passphrase": "wrong passphrase"})
    assert response.status_code == 401


def test_login_rate_limit_returns_429_after_five_failures(client: TestClient):
    for _ in range(4):
        response = client.post("/auth/login", json={"passphrase": "wrong"})
        assert response.status_code == 401

    response = client.post("/auth/login", json={"passphrase": "wrong"})
    assert response.status_code == 429


def test_protected_route_without_token_returns_401(client: TestClient):
    response = client.get("/attributes")
    assert response.status_code == 401
    assert response.json() == {"error": "authentication required"}


def test_protected_route_with_valid_token_returns_200(client: TestClient):
    response = client.get("/attributes", headers=_login_headers(client))
    assert response.status_code == 200


def test_protected_route_with_expired_token_returns_401(client: TestClient):
    _app(client).state.active_sessions["expired"] = (
        datetime.now(UTC) - timedelta(minutes=1)
    )
    response = client.get("/attributes", headers={"Authorization": "Bearer expired"})
    assert response.status_code == 401


def test_get_attributes_returns_list_of_active_attributes(client: TestClient):
    _insert_attribute(_db(client), "goals", "priority", "Finish phase 3")
    _insert_attribute(
        _db(client),
        "goals",
        "old_priority",
        "Old",
        status="superseded",
    )

    response = client.get("/attributes", headers=_login_headers(client))
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["label"] == "priority"


def test_put_attribute_value_creates_history_record(client: TestClient):
    attribute_id = _insert_attribute(
        _db(client),
        "goals",
        "priority",
        "Finish phase 3",
    )

    response = client.put(
        f"/attributes/{attribute_id}",
        json={"value": "Finish phase 3a"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    new_id = response.json()["id"]
    assert new_id != attribute_id

    old_status = _db(client).execute(
        "SELECT status FROM attributes WHERE id = ?",
        (attribute_id,),
    ).fetchone()[0]
    assert old_status == "superseded"

    history = _db(client).execute(
        "SELECT attribute_id, previous_value, changed_by FROM attribute_history"
    ).fetchone()
    assert history == (attribute_id, "Finish phase 3", "user")


def test_put_attribute_routing_guard_blocks_external_ok_on_protected_domains(client: TestClient):
    attribute_id = _insert_attribute(
        _db(client),
        "beliefs",
        "worldview",
        "I value honesty.",
    )

    response = client.put(
        f"/attributes/{attribute_id}",
        json={"routing": "external_ok"},
        headers=_login_headers(client),
    )

    assert response.status_code == 403
    assert response.json()["error"] == "routing_protected"


def test_capture_preview_does_not_write_to_database(client: TestClient, monkeypatch):
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "patterns",
                "label": "morning_focus",
                "value": "I focus best in the morning.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            }
        ],
    )

    response = client.post(
        "/capture/preview",
        json={"text": "I focus best in the morning."},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["proposed"][0]["label"] == "morning_focus"
    count = _db(client).execute("SELECT count(*) FROM attributes").fetchone()[0]
    assert count == 0


def test_capture_preview_defaults_missing_confidence(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.capture.preview_capture",
        lambda text, domain_hint, provider_config: [
            {
                "domain": "patterns",
                "label": "morning_focus",
                "value": "I focus best in the morning.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.5,
            }
        ],
    )

    response = client.post(
        "/capture/preview",
        json={"text": "I focus best in the morning.", "domain_hint": "patterns"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["proposed"][0]["confidence"] == pytest.approx(0.5)


def test_capture_writes_attributes_with_local_only_routing(client: TestClient, monkeypatch):
    _mock_capture_extraction(
        monkeypatch,
        [
            {
                "domain": "goals",
                "label": "phase_three",
                "value": "I want the FastAPI backend live.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.7,
            }
        ],
    )

    response = client.post(
        "/capture",
        json={"text": "I want the FastAPI backend live.", "domain_hint": "goals"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["attributes_saved"] == 1
    routing = _db(client).execute(
        "SELECT routing FROM attributes WHERE label = 'phase_three'"
    ).fetchone()[0]
    assert routing == "local_only"


def test_query_returns_409_when_external_routing_violates_local_only_policy(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(
        "server.routes.query.resolve_external_router",
        lambda: ProviderConfig(
            provider="anthropic",
            api_key="test-key",  # pragma: allowlist secret
            model="claude-sonnet-4-6",
            is_local=False,
            arch="apple_silicon",
            ram_gb=36.0,
        ),
    )
    monkeypatch.setattr(
        "server.routes.query.prepare_query",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RoutingViolationError("local_only attributes cannot be sent externally")
        ),
    )

    response = client.post(
        "/query",
        json={"query": "Tell me about my fears", "backend_override": "external"},
        headers=_login_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"] == "routing_violation"


def test_query_stream_emits_upstream_error_details(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.query.resolve_external_router",
        lambda: ProviderConfig(
            provider="anthropic",
            api_key="test-key",  # pragma: allowlist secret
            model="claude-sonnet-4-6",
            is_local=False,
            arch="apple_silicon",
            ram_gb=36.0,
        ),
    )
    monkeypatch.setattr(
        "server.routes.query.PrivacyBroker.generate_grounded_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.exceptions.Timeout()),
    )

    response = client.post(
        "/query/stream",
        json={"query": "What matters most to me?", "backend_override": "external"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.text
    assert '"type": "error"' in body
    assert '"code": "upstream_error"' in body


def test_capture_accepts_preview_items_and_supersedes_conflicts(client: TestClient):
    existing_id = _insert_attribute(
        _db(client),
        "goals",
        "phase_three",
        "Old goal",
    )

    response = client.post(
        "/capture",
        json={
            "text": "ignored when accepted items are supplied",
            "accepted": [
                {
                    "domain": "goals",
                    "label": "phase_three",
                    "value": "New goal",
                    "elaboration": None,
                    "mutability": "evolving",
                    "confidence": 0.7,
                }
            ],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["attributes_saved"] == 1

    previous_status = _db(client).execute(
        "SELECT status FROM attributes WHERE id = ?",
        (existing_id,),
    ).fetchone()[0]
    assert previous_status == "superseded"

    active_value = _db(client).execute(
        "SELECT value FROM attributes WHERE label = ? AND status = 'active'",
        ("phase_three",),
    ).fetchone()[0]
    assert active_value == "New goal"


def test_sessions_include_routing_log_entries(client: TestClient):
    _db(client).execute(
        """
        INSERT INTO reflection_sessions (
            id,
            session_type,
            summary,
            attributes_created,
            attributes_updated,
            external_calls_made,
            routing_log,
            started_at,
            ended_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            "freeform",
            "1 queries across session",
            0,
            0,
            1,
            json.dumps(
                [
                    {
                        "query": "What matters most to me right now?",
                        "query_type": "open_ended",
                        "backend": "external",
                        "attribute_count": 4,
                        "domains_referenced": ["goals", "values"],
                        "timestamp": "2026-04-08T12:00:00+00:00",
                    }
                ]
            ),
            "2026-04-08T12:00:00+00:00",
            "2026-04-08T12:05:00+00:00",
        ),
    )
    _db(client).commit()

    response = client.get("/sessions", headers=_login_headers(client))

    assert response.status_code == 200
    session = response.json()[0]
    assert session["routing_log"][0]["backend"] == "external"
    assert session["routing_log"][0]["domains_referenced"] == ["goals", "values"]


def test_server_startup_asserts_bind_ip_is_not_zero_zero_zero_zero():
    with pytest.raises(RuntimeError):
        assert_safe_bind_ip("0.0.0.0")


def test_security_headers_present_on_all_responses(client: TestClient):
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
