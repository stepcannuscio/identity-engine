"""Tests for the FastAPI backend server."""

from __future__ import annotations

import json
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import zlib
from zipfile import ZipFile

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine.capture as capture_module
from config.llm_router import ProviderConfig
from db.connection import get_plain_connection
from db.inference_evidence import InferenceEvidenceInput, record_inference_evidence_batch
from db.schema import create_tables, seed_domains
from engine.privacy_broker import InferenceDecision
from engine.prompt_builder import RoutingViolationError
from engine.setup_state import get_provider_statuses
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


def _external_config() -> ProviderConfig:
    return ProviderConfig(
        provider="anthropic",
        api_key="test-key",  # pragma: allowlist secret
        model="claude-sonnet-4-6",
        is_local=False,
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
    source: str = "explicit",
) -> str:
    now = "2026-04-08T12:00:00+00:00"
    attribute_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO attributes (
            id, domain_id, label, value, elaboration, mutability, source, confidence,
            routing, status, created_at, updated_at, last_confirmed
        )
        VALUES (?, ?, ?, ?, ?, 'stable', ?, 0.8, ?, ?, ?, ?, ?)
        """,
        (
            attribute_id,
            _domain_id(conn, domain),
            label,
            value,
            None,
            source,
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
        lambda self, messages, task_type="capture_extraction", **kwargs: SimpleNamespace(
            content=json.dumps(attrs),
            metadata=SimpleNamespace(task_type=task_type),
        ),
    )


def _mock_teach_question_generation(monkeypatch) -> None:
    monkeypatch.setattr(
        "engine.teach_planner.PrivacyBroker.generate_grounded_response",
        lambda self, messages, **kwargs: SimpleNamespace(
            content=json.dumps(
                {
                    "question": "What feels most important to teach next?",
                    "intent_key": "generated_follow_up",
                }
            ),
            metadata=SimpleNamespace(task_type=kwargs.get("task_type", "teach_question_generation")),
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
    monkeypatch.setattr("server.main.start_worker", lambda config: None)
    monkeypatch.setattr("server.main.stop_worker", lambda: None)
    monkeypatch.setattr("server.routes.artifacts.enqueue_analysis", lambda artifact_id: None)
    monkeypatch.setattr(
        "server.auth.get_ui_passphrase",
        lambda: "correct horse battery staple",
    )
    _mock_teach_question_generation(monkeypatch)

    db_context = get_plain_connection(":memory:")
    conn = db_context.__enter__()
    create_tables(conn)
    seed_domains(conn)

    @contextmanager
    def _get_db_connection():
        yield conn

    monkeypatch.setattr("server.main.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.query.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.artifacts.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.attributes.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.capture.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.evidence.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.interview.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.preferences.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.session.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.setup.get_db_connection", _get_db_connection)
    monkeypatch.setattr("server.routes.teach.get_db_connection", _get_db_connection)
    monkeypatch.setattr("engine.setup_state._ollama_is_running", lambda: False)
    monkeypatch.setattr("engine.setup_state._ollama_has_model", lambda model: False)
    monkeypatch.setattr("engine.setup_state.has_api_key", lambda provider: False)

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


def _simple_pdf_bytes(text: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>endobj\n"
        + f"4 0 obj<< /Length {len(content)} >>stream\n".encode("latin-1")
        + content
        + b"\nendstream endobj\ntrailer<< /Root 1 0 R >>\n%%EOF"
    )


def _compressed_pdf_bytes(text: str) -> bytes:
    content = zlib.compress(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>endobj\n"
        + f"4 0 obj<< /Length {len(content)} /Filter /FlateDecode >>stream\n".encode("latin-1")
        + content
        + b"\nendstream endobj\ntrailer<< /Root 1 0 R >>\n%%EOF"
    )


def _simple_docx_bytes(text: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>"
                f"{text}"
                "</w:t></w:r></w:p></w:body></w:document>"
            ),
        )
    return buffer.getvalue()


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


def test_get_provider_statuses_is_read_only_by_default(monkeypatch):
    class RecordingConnection:
        def __init__(self):
            self.executemany_calls = 0
            self.commit_calls = 0

        def executemany(self, *_args, **_kwargs):
            self.executemany_calls += 1
            raise AssertionError("get_provider_statuses() should not persist without persist=True")

        def commit(self):
            self.commit_calls += 1
            raise AssertionError("get_provider_statuses() should not commit without persist=True")

    monkeypatch.setattr("engine.setup_state.detect_hardware", lambda: {"recommended_tier": "local_small"})
    monkeypatch.setattr("engine.setup_state._ollama_is_running", lambda: True)
    monkeypatch.setattr("engine.setup_state._ollama_has_model", lambda model: True)
    monkeypatch.setattr("engine.setup_state.has_api_key", lambda provider: provider == "anthropic")

    conn = RecordingConnection()
    statuses = get_provider_statuses(conn)

    assert statuses
    assert conn.executemany_calls == 0
    assert conn.commit_calls == 0


def test_get_provider_statuses_can_persist_when_requested(monkeypatch):
    monkeypatch.setattr("engine.setup_state.detect_hardware", lambda: {"recommended_tier": "local_small"})
    monkeypatch.setattr("engine.setup_state._ollama_is_running", lambda: True)
    monkeypatch.setattr("engine.setup_state._ollama_has_model", lambda model: True)
    monkeypatch.setattr("engine.setup_state.has_api_key", lambda provider: provider == "anthropic")

    with get_plain_connection(":memory:") as conn:
        create_tables(conn)
        statuses = get_provider_statuses(conn, persist=True)
        stored = conn.execute(
            "SELECT provider, configured, validated FROM provider_status ORDER BY provider"
        ).fetchall()

    assert statuses
    assert stored
    assert any(row[0] == "anthropic" and row[1] == 1 and row[2] == 1 for row in stored)


def test_protected_route_without_token_returns_401(client: TestClient):
    response = client.get("/attributes")
    assert response.status_code == 401
    assert response.json() == {"error": "authentication required"}


def test_protected_route_with_valid_token_returns_200(client: TestClient):
    response = client.get("/attributes", headers=_login_headers(client))
    assert response.status_code == 200


def test_preference_route_without_token_returns_401(client: TestClient):
    response = client.get("/preferences/signals")
    assert response.status_code == 401
    assert response.json() == {"error": "authentication required"}


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


def test_post_preference_signal_creates_local_preference_record(client: TestClient):
    response = client.post(
        "/preferences/signals",
        json={
            "category": "writing_style",
            "subject": "concise_responses",
            "signal": "prefer",
            "strength": 4,
            "source": "explicit_feedback",
            "context": {"audience": "work"},
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "writing_style"
    assert body["subject"] == "concise_responses"
    assert body["signal"] == "prefer"
    assert body["context"] == {"audience": "work"}

    stored = _db(client).execute(
        "SELECT category, subject, signal, strength, source FROM preference_signals"
    ).fetchone()
    assert stored == (
        "writing_style",
        "concise_responses",
        "prefer",
        4,
        "explicit_feedback",
    )


def test_get_preference_signals_supports_filters(client: TestClient):
    _db(client).execute(
        """
        INSERT INTO preference_signals (
            id, category, subject, signal, strength, source, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            "writing_style",
            "concise_responses",
            "prefer",
            4,
            "explicit_feedback",
            "2026-04-17T12:00:00+00:00",
        ),
    )
    _db(client).execute(
        """
        INSERT INTO preference_signals (
            id, category, subject, signal, strength, source, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            "books",
            "history",
            "like",
            3,
            "explicit_feedback",
            "2026-04-17T12:05:00+00:00",
        ),
    )
    _db(client).commit()

    response = client.get(
        "/preferences/signals",
        params={"category": "writing_style"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["subject"] == "concise_responses"


def test_get_preference_signal_summary_returns_net_scores(client: TestClient):
    for signal, strength, created_at in [
        ("prefer", 4, "2026-04-17T12:00:00+00:00"),
        ("avoid", 2, "2026-04-17T12:01:00+00:00"),
    ]:
        _db(client).execute(
            """
            INSERT INTO preference_signals (
                id, category, subject, signal, strength, source, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "writing_style",
                "concise_responses",
                signal,
                strength,
                "explicit_feedback",
                created_at,
            ),
        )
    _db(client).commit()

    response = client.get(
        "/preferences/signals/summary",
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "category": "writing_style",
            "subject": "concise_responses",
            "observations": 2,
            "positive_count": 1,
            "negative_count": 1,
            "net_score": 2,
            "latest_at": "2026-04-17T12:01:00Z",
        }
    ]


def test_post_preference_signal_invalid_payload_fails_clearly(client: TestClient):
    response = client.post(
        "/preferences/signals",
        json={
            "category": "writing_style",
            "subject": "concise_responses",
            "signal": "prefer",
            "strength": 9,
            "source": "explicit_feedback",
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Preference signal strength must be between 1 and 5."


def test_preference_signal_writes_do_not_touch_routing_logs(client: TestClient):
    response = client.post(
        "/preferences/signals",
        json={
            "category": "writing_style",
            "subject": "concise_responses",
            "signal": "prefer",
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert _app(client).state.current_session.routing_log == []


def test_post_preference_promote_creates_attribute_and_evidence(client: TestClient):
    headers = _login_headers(client)
    for _ in range(3):
        response = client.post(
            "/preferences/signals",
            json={
                "category": "writing_style",
                "subject": "concise_responses",
                "signal": "prefer",
            },
            headers=headers,
        )
        assert response.status_code == 200

    response = client.post(
        "/preferences/promote",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["category"] == "writing_style"
    assert body[0]["subject"] == "concise_responses"
    assert body[0]["state"] == "stable"
    assert body[0]["action"] == "created"
    assert body[0]["domain"] == "voice"
    assert body[0]["label"] == "preference_writing_style_concise_responses"
    assert body[0]["attribute_id"]
    assert body[0]["confidence"] == 0.91
    assert body[0]["observations"] == 3
    assert body[0]["positive_count"] == 3
    assert body[0]["negative_count"] == 0
    assert body[0]["net_score"] == 9

    stored = _db(client).execute(
        """
        SELECT source, routing, status
        FROM attributes
        WHERE label = ?
        """,
        ("preference_writing_style_concise_responses",),
    ).fetchone()
    assert stored == ("inferred", "local_only", "active")

    evidence_count = _db(client).execute(
        """
        SELECT count(*)
        FROM inference_evidence
        WHERE source_type = 'preference_signal'
        """
    ).fetchone()[0]
    assert evidence_count == 3


def test_post_preference_promote_respects_rejected_attribute(client: TestClient):
    headers = _login_headers(client)
    for _ in range(3):
        _db(client).execute(
            """
            INSERT INTO preference_signals (
                id, category, subject, signal, strength, source, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "writing_style",
                "concise_responses",
                "prefer",
                3,
                "explicit_feedback",
                "2026-04-17T12:00:00+00:00",
            ),
        )
    _db(client).commit()
    rejected_id = _insert_attribute(
        _db(client),
        "voice",
        "preference_writing_style_concise_responses",
        "I prefer concise responses.",
        status="rejected",
        source="inferred",
    )

    response = client.post(
        "/preferences/promote",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()[0]["action"] == "blocked_rejected"
    assert response.json()[0]["attribute_id"] == rejected_id


def test_get_attribute_provenance_requires_authentication(client: TestClient):
    attribute_id = _insert_attribute(
        _db(client),
        "patterns",
        "focus_window",
        "Best in the morning",
        source="inferred",
    )

    response = client.get(f"/attributes/{attribute_id}/provenance")

    assert response.status_code == 401
    assert response.json() == {"error": "authentication required"}


def test_get_attribute_provenance_returns_summaries_without_raw_supporting_text(
    client: TestClient,
):
    attribute_id = _insert_attribute(
        _db(client),
        "voice",
        "writing_style",
        "Prefers concise writing",
        source="inferred",
    )
    private_text = "I want fewer words and tighter phrasing in every update."
    record_inference_evidence_batch(
        _db(client),
        attribute_id,
        [
            InferenceEvidenceInput(
                source_type="journal",
                source_ref="journal-17",
                supporting_text=private_text,
                weight=0.8,
            )
        ],
    )

    response = client.get(
        f"/attributes/{attribute_id}/provenance",
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["attribute_id"] == attribute_id
    assert body["label"] == "writing_style"
    assert body["source"] == "inferred"
    assert body["evidence"] == [
        {
            "source_type": "journal",
            "summary": "Derived from journal entry; 10-word supporting note kept local.",
            "weight": pytest.approx(0.8),
        }
    ]
    assert private_text not in response.text


def test_get_attribute_provenance_returns_empty_evidence_for_inferred_attribute_without_rows(
    client: TestClient,
):
    attribute_id = _insert_attribute(
        _db(client),
        "patterns",
        "energy_pattern",
        "Needs quiet recovery after meetings",
        source="inferred",
    )

    response = client.get(
        f"/attributes/{attribute_id}/provenance",
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json() == {
        "attribute_id": attribute_id,
        "label": "energy_pattern",
        "source": "inferred",
        "evidence": [],
    }


def test_get_attribute_provenance_returns_multiple_rows_in_creation_order(client: TestClient):
    attribute_id = _insert_attribute(
        _db(client),
        "patterns",
        "meeting_load",
        "Too many meetings drains energy",
        source="inferred",
    )
    record_inference_evidence_batch(
        _db(client),
        attribute_id,
        [
            InferenceEvidenceInput(
                source_type="capture",
                supporting_text="Back-to-back meetings drain me.",
                weight=0.6,
            ),
            InferenceEvidenceInput(
                source_type="reflection_session",
                source_ref="session-7",
                supporting_text="I need recovery time after meeting-heavy days.",
                weight=0.9,
            ),
        ],
    )

    response = client.get(
        f"/attributes/{attribute_id}/provenance",
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["evidence"] == [
        {
            "source_type": "capture",
            "summary": "Derived from captured note; 6-word supporting note kept local.",
            "weight": pytest.approx(0.6),
        },
        {
            "source_type": "reflection_session",
            "summary": "Derived from reflection session; 8-word supporting note kept local.",
            "weight": pytest.approx(0.9),
        },
    ]


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


def test_patch_attribute_confirm_marks_confirmed_and_writes_history(client: TestClient):
    attribute_id = _insert_attribute(
        _db(client),
        "values",
        "honesty",
        "Honesty matters most",
    )

    response = client.patch(
        f"/attributes/{attribute_id}",
        json={"action": "confirm"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert response.json()["last_confirmed"] is not None

    history = _db(client).execute(
        "SELECT attribute_id, previous_value, reason, changed_by FROM attribute_history"
    ).fetchone()
    assert history == (attribute_id, "Honesty matters most", "confirm", "user")


def test_patch_attribute_reject_marks_rejected_and_removes_from_listing(client: TestClient):
    attribute_id = _insert_attribute(
        _db(client),
        "goals",
        "priority",
        "Finish phase 3",
    )

    response = client.patch(
        f"/attributes/{attribute_id}",
        json={"action": "reject"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    listed = client.get("/attributes", headers=_login_headers(client))
    assert listed.status_code == 200
    assert listed.json() == []


def test_patch_attribute_refine_creates_new_version_and_preserves_old_record(client: TestClient):
    attribute_id = _insert_attribute(
        _db(client),
        "goals",
        "priority",
        "Finish phase 3",
    )

    response = client.patch(
        f"/attributes/{attribute_id}",
        json={"action": "refine", "new_value": "Finish phase 3a"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] != attribute_id
    assert body["status"] == "active"
    assert body["value"] == "Finish phase 3a"

    old_status = _db(client).execute(
        "SELECT status FROM attributes WHERE id = ?",
        (attribute_id,),
    ).fetchone()[0]
    assert old_status == "superseded"

    history = _db(client).execute(
        "SELECT attribute_id, previous_value, reason FROM attribute_history"
    ).fetchone()
    assert history == (attribute_id, "Finish phase 3", "refine")


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


def test_capture_preview_requires_consent_for_external_extraction(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.capture.resolve_active_provider_config",
        lambda conn, default_config: _external_config(),
    )

    response = client.post(
        "/capture/preview",
        json={"text": "I focus best in the morning."},
        headers=_login_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"] == "external_extraction_consent_required"


def test_capture_preview_allows_external_extraction_after_consent(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.capture.resolve_active_provider_config",
        lambda conn, default_config: _external_config(),
    )
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
        json={
            "text": "I focus best in the morning.",
            "allow_external_extraction": True,
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["proposed"][0]["label"] == "morning_focus"


def test_interview_preview_does_not_write_to_database(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.interview.preview_interview_answer",
        lambda question, answer, domain_name, provider_config: [
            {
                "domain": "goals",
                "label": "career_priority",
                "value": "I want to ship the backend cleanly this quarter.",
                "elaboration": None,
                "mutability": "evolving",
                "confidence": 0.8,
            }
        ],
    )

    response = client.post(
        "/interview/preview",
        json={
            "domain": "goals",
            "question": "What is the most important thing you are trying to achieve in the next six months, professionally?",
            "answer": "Ship the backend cleanly this quarter.",
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["proposed"][0]["label"] == "career_priority"
    count = _db(client).execute("SELECT count(*) FROM attributes").fetchone()[0]
    assert count == 0


def test_interview_preview_requires_consent_for_external_extraction(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.interview.resolve_active_provider_config",
        lambda conn, default_config: _external_config(),
    )

    response = client.post(
        "/interview/preview",
        json={
            "domain": "goals",
            "question": "What is the most important thing you are trying to achieve in the next six months, professionally?",
            "answer": "Ship the backend cleanly this quarter.",
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"] == "external_extraction_consent_required"


def test_interview_writes_attributes_with_reflection_source(client: TestClient):
    response = client.post(
        "/interview",
        json={
            "domain": "goals",
            "question": "What is the most important thing you are trying to achieve in the next six months, professionally?",
            "answer": "Ship the backend cleanly this quarter.",
            "accepted": [
                {
                    "domain": "goals",
                    "label": "career_priority",
                    "value": "I want to ship the backend cleanly this quarter.",
                    "elaboration": None,
                    "mutability": "evolving",
                    "confidence": 0.8,
                }
            ],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["attributes_saved"] == 1
    stored = _db(client).execute(
        "SELECT source, routing FROM attributes WHERE label = 'career_priority'"
    ).fetchone()
    assert stored == ("reflection", "local_only")


def test_interview_preview_rejects_invalid_question(client: TestClient):
    response = client.post(
        "/interview/preview",
        json={
            "domain": "goals",
            "question": "What is your favorite color?",
            "answer": "Blue.",
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]


def test_post_artifacts_accepts_json_text(client: TestClient):
    response = client.post(
        "/artifacts",
        json={
            "text": "These are my working notes about concise writing and revision.",
            "title": "Writing notes",
            "type": "note",
            "source": "capture",
            "domain": "voice",
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_id"]
    assert body["chunk_count"] == 1

    stored = _db(client).execute(
        "SELECT title, type, source FROM artifacts"
    ).fetchone()
    assert stored == ("Writing notes", "note", "capture")


def test_post_artifacts_accepts_text_file_upload(client: TestClient):
    response = client.post(
        "/artifacts",
        data={"title": "Meeting transcript", "domain": "patterns"},
        files={"file": (
            "transcript.md", b"Meetings drain me when they stack back to back.", "text/markdown"
        )},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_count"] == 1

    stored = _db(client).execute(
        "SELECT title, source FROM artifacts"
    ).fetchone()
    assert stored == ("Meeting transcript", "upload")


def test_post_artifacts_rejects_text_and_file_together(client: TestClient):
    response = client.post(
        "/artifacts",
        data={"text": "duplicate", "title": "Bad upload"},
        files={"file": ("bad.txt", b"also here", "text/plain")},
        headers=_login_headers(client),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "provide either text or file, not both"


def test_post_artifacts_rejects_oversized_content_length(client: TestClient):
    headers = _login_headers(client)
    headers["Content-Length"] = str((5 * 1024 * 1024) + 1)

    response = client.post(
        "/artifacts",
        json={"text": "short note"},
        headers=headers,
    )

    assert response.status_code == 413


def test_post_artifacts_rejects_oversized_json_text(client: TestClient):
    response = client.post(
        "/artifacts",
        json={"text": "x" * 250001},
        headers=_login_headers(client),
    )

    assert response.status_code == 413


def test_post_artifacts_rejects_oversized_file_upload(client: TestClient):
    response = client.post(
        "/artifacts",
        files={"file": ("large.txt", b"x" * ((5 * 1024 * 1024) + 1), "text/plain")},
        headers=_login_headers(client),
    )

    assert response.status_code == 413


def test_post_artifacts_rejects_docx_with_oversized_document_xml(client: TestClient):
    response = client.post(
        "/artifacts",
        files={
            "file": (
                "huge.docx",
                _simple_docx_bytes("x" * ((2 * 1024 * 1024) + 1)),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unable to extract text from docx"


def test_post_artifacts_rejects_extracted_text_over_limit(client: TestClient):
    response = client.post(
        "/artifacts",
        files={
            "file": (
                "huge.pdf",
                _simple_pdf_bytes("x" * 250001),
                "application/pdf",
            )
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 413


def test_query_returns_409_when_external_routing_violates_local_only_policy(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(
        "server.routes.query.resolve_active_provider_config",
        lambda *args, **kwargs: ProviderConfig(
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
    assert response.json()["privacy"]["execution_mode"] == "blocked"
    assert "local-only data" in response.json()["privacy"]["summary"]


def test_query_response_includes_normalized_privacy_metadata(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(
        "server.routes.query.prepare_query",
        lambda *args, **kwargs: SimpleNamespace(
            query="What matters most to me right now?",
            query_type="open_ended",
            attributes=[
                {"domain": "goals", "routing": "external_ok"},
                {"domain": "values", "routing": "external_ok"},
            ],
            messages=[{"role": "user", "content": "What matters most to me right now?"}],
            backend="local",
            assembled_context=SimpleNamespace(
                contains_local_only=False,
                domains_used=["goals", "values"],
            ),
            coverage=SimpleNamespace(
                counts=SimpleNamespace(attributes=2, preferences=0, artifacts=0),
                confidence="medium_confidence",
                notes=None,
            ),
        ),
    )
    monkeypatch.setattr(
        "server.routes.query.PrivacyBroker.generate_grounded_response",
        lambda *args, **kwargs: SimpleNamespace(
            content="Focus on long-term work and honest relationships.",
            metadata=InferenceDecision(
                provider="ollama",
                model="llama3.1:8b",
                is_local=True,
                task_type="query_generation",
                blocked_external_attributes_count=0,
                routing_enforced=True,
                attribute_count=2,
                domains_used=["goals", "values"],
                retrieval_mode="open_ended",
                contains_local_only_context=False,
            ),
        ),
    )

    response = client.post(
        "/query",
        json={"query": "What matters most to me right now?"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["privacy"]["execution_mode"] == "local"
    assert body["metadata"]["privacy"]["routing_enforced"] is True
    assert body["metadata"]["privacy"]["summary"] == "Processed locally with privacy rules applied."
    assert body["metadata"]["confidence"] == "medium_confidence"
    assert body["metadata"]["coverage"] == {
        "attributes": 2,
        "preferences": 0,
        "artifacts": 0,
    }
    assert body["metadata"]["acquisition"] == {
        "status": "not_needed",
        "gaps": [],
        "suggestions": [],
    }


def test_query_response_includes_acquisition_metadata(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.query.prepare_query",
        lambda *args, **kwargs: SimpleNamespace(
            query="What are my current goals?",
            query_type="simple",
            attributes=[],
            messages=[{"role": "user", "content": "What are my current goals?"}],
            backend="local",
            acquisition=SimpleNamespace(
                status="suggested",
                gaps=[
                    SimpleNamespace(
                        kind="identity",
                        domain="goals",
                        reason="No strong current identity coverage was retrieved for this domain.",
                    )
                ],
                suggestions=[
                    SimpleNamespace(
                        kind="quick_capture",
                        prompt="I don't know much about your goals yet.",
                        action={"target": "attribute", "domain_hint": "goals"},
                    )
                ],
            ),
            assembled_context=SimpleNamespace(
                contains_local_only=False,
                domains_used=[],
            ),
            coverage=SimpleNamespace(
                counts=SimpleNamespace(attributes=0, preferences=0, artifacts=0),
                confidence="low_confidence",
                notes="Only thin context was available.",
            ),
        ),
    )
    monkeypatch.setattr(
        "server.routes.query.PrivacyBroker.generate_grounded_response",
        lambda *args, **kwargs: SimpleNamespace(
            content="I only have thin context for your goals right now.",
            metadata=InferenceDecision(
                provider="ollama",
                model="llama3.1:8b",
                is_local=True,
                task_type="query_generation",
                blocked_external_attributes_count=0,
                routing_enforced=True,
                attribute_count=0,
                domains_used=[],
                retrieval_mode="simple",
                contains_local_only_context=False,
            ),
        ),
    )

    response = client.post(
        "/query",
        json={"query": "What are my current goals?"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    acquisition = response.json()["metadata"]["acquisition"]
    assert acquisition["status"] == "suggested"
    assert acquisition["gaps"][0]["kind"] == "identity"
    assert acquisition["gaps"][0]["domain"] == "goals"
    assert acquisition["suggestions"][0]["kind"] == "quick_capture"


def test_query_returns_insufficient_data_message_without_calling_broker(
    client: TestClient, monkeypatch
):
    """Coverage short-circuit returns canned message on an empty identity store."""
    called = {"value": False}

    def _unexpected_broker(*args, **kwargs):
        called["value"] = True
        raise AssertionError("broker should not be called for insufficient data")

    monkeypatch.setattr(
        "server.routes.query.PrivacyBroker.generate_grounded_response",
        _unexpected_broker,
    )

    response = client.post(
        "/query",
        json={"query": "What should I do about nothing in particular?"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["confidence"] == "insufficient_data"
    assert body["metadata"]["coverage"] == {
        "attributes": 0,
        "preferences": 0,
        "artifacts": 0,
    }
    assert "acquisition" in body["metadata"]
    assert "enough grounded context" in body["response"]
    assert called["value"] is False


def test_query_stream_emits_privacy_metadata(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.query.prepare_query",
        lambda *args, **kwargs: SimpleNamespace(
            query="What matters most to me right now?",
            query_type="open_ended",
            attributes=[{"domain": "goals", "routing": "external_ok"}],
            messages=[{"role": "user", "content": "What matters most to me right now?"}],
            backend="external",
            assembled_context=SimpleNamespace(
                contains_local_only=False,
                domains_used=["goals"],
            ),
            coverage=SimpleNamespace(
                counts=SimpleNamespace(attributes=1, preferences=0, artifacts=0),
                confidence="medium_confidence",
                notes=None,
            ),
        ),
    )
    monkeypatch.setattr(
        "server.routes.query.resolve_active_provider_config",
        lambda *args, **kwargs: ProviderConfig(
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
        lambda *args, **kwargs: SimpleNamespace(
            content=iter(["Trust", " your", " own", " record."]),
            metadata=InferenceDecision(
                provider="anthropic",
                model="claude-sonnet-4-6",
                is_local=False,
                task_type="query_generation",
                blocked_external_attributes_count=0,
                routing_enforced=True,
                attribute_count=1,
                domains_used=["goals"],
                retrieval_mode="open_ended",
                contains_local_only_context=False,
            ),
        ),
    )

    response = client.post(
        "/query/stream",
        json={"query": "What matters most to me right now?", "backend_override": "external"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.text
    assert '"type": "metadata"' in body
    assert '"execution_mode": "external"' in body
    assert '"summary": "Used an external model after privacy rules were applied."' in body
    assert '"acquisition"' in body


def test_query_stream_emits_upstream_error_details(client: TestClient, monkeypatch):
    # Three confirmed, external_ok attributes in the goals domain to clear the
    # 25-pt insufficient threshold (each confirmed + 0.8-conf = 13 pts; 3×13=39).
    _db_conn = _db(client)
    for label, value in [
        ("priority", "Ship the phase 3 milestone this quarter."),
        ("secondary_goal", "Read one technical book per month."),
        ("long_term_goal", "Build a sustainable freelance practice."),
    ]:
        _insert_attribute(
            _db_conn, "goals", label, value, routing="external_ok", status="confirmed"
        )
    monkeypatch.setattr(
        "server.routes.query.resolve_active_provider_config",
        lambda *args, **kwargs: ProviderConfig(
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
        json={"query": "What are my goals?", "backend_override": "external"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.text
    assert '"type": "error"' in body
    assert '"code": "upstream_error"' in body


def test_post_query_feedback_stores_local_feedback_row(client: TestClient):
    response = client.post(
        "/query/feedback",
        json={
            "query": "How should I plan my week?",
            "response": "Protect your focus blocks and group shallow work.",
            "feedback": "helpful",
            "notes": "The focus-block guidance matched my working style.",
            "query_type": "simple",
            "backend_used": "local",
            "confidence": "medium_confidence",
            "intent": {
                "source_profile": "preference_sensitive",
                "intent_tags": ["planning"],
                "domain_hints": ["goals", "patterns"],
            },
            "domains_referenced": ["goals", "patterns"],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    feedback_id = response.json()["id"]
    row = _db(client).execute(
        """
        SELECT query_text, response_text, feedback, notes, backend, source_profile
        FROM query_feedback
        WHERE id = ?
        """,
        (feedback_id,),
    ).fetchone()
    assert row == (
        "How should I plan my week?",
        "Protect your focus blocks and group shallow work.",
        "helpful",
        "The focus-block guidance matched my working style.",
        "local",
        "preference_sensitive",
    )

    evidence_rows = _db(client).execute(
        """
        SELECT er.kind, er.summary, el.target_type, el.target_id
        FROM evidence_records er
        JOIN evidence_links el ON el.evidence_id = er.id
        WHERE er.origin_table = 'query_feedback' AND er.origin_id = ?
        ORDER BY el.target_type, el.target_id
        """,
        (feedback_id,),
    ).fetchall()
    assert evidence_rows == [
        (
            "query_feedback",
            "Local query feedback marked as helpful for a local response.",
            "query_feedback",
            feedback_id,
        ),
        (
            "query_feedback",
            "Local query feedback marked as helpful for a local response.",
            "session",
            str(getattr(_app(client).state.current_session, "id")),
        ),
    ]


def test_post_query_feedback_triggers_retrieval_calibration_after_batch(client: TestClient):
    headers = _login_headers(client)
    payload = {
        "query": "How should I plan my week?",
        "response": "Protect your focus blocks and group shallow work.",
        "query_type": "simple",
        "backend_used": "local",
        "confidence": "low_confidence",
        "intent": {
            "source_profile": "preference_sensitive",
            "intent_tags": ["planning"],
            "domain_hints": ["goals"],
        },
        "domains_referenced": ["goals"],
    }

    for _ in range(7):
        response = client.post(
            "/query/feedback",
            json={**payload, "feedback": "missed_context"},
            headers=headers,
        )
        assert response.status_code == 200

    for _ in range(3):
        response = client.post(
            "/query/feedback",
            json={**payload, "feedback": "helpful"},
            headers=headers,
        )
        assert response.status_code == 200

    rows = _db(client).execute(
        """
        SELECT feedback_pattern, score_delta, observation_count
        FROM retrieval_calibration
        WHERE domain = 'goals' AND source_profile = 'preference_sensitive'
        ORDER BY feedback_pattern
        """
    ).fetchall()

    assert len(rows) == 4
    assert {row[0] for row in rows} == {
        "helpful",
        "missed_context",
        "ungrounded",
        "wrong_focus",
    }
    assert all(row[2] == 10 for row in rows)
    assert all(row[1] is not None for row in rows)


def test_post_query_feedback_stores_voice_feedback_and_signal(client: TestClient):
    response = client.post(
        "/query/feedback",
        json={
            "query": "Rewrite this email so it sounds like me.",
            "response": "Thanks for the note. I want to keep this direct and calm.",
            "feedback": "wrong_focus",
            "voice_feedback": "too_formal",
            "notes": "It still sounds too polished.",
            "query_type": "simple",
            "backend_used": "local",
            "confidence": "medium_confidence",
            "intent": {
                "source_profile": "voice_generation",
                "intent_tags": ["voice_adaptation", "writing_task"],
                "domain_hints": ["voice"],
            },
            "domains_referenced": ["voice"],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    feedback_id = response.json()["id"]
    row = _db(client).execute(
        """
        SELECT query_feedback_id, feedback, notes, backend
        FROM voice_feedback
        WHERE query_feedback_id = ?
        """,
        (feedback_id,),
    ).fetchone()
    assert row == (
        feedback_id,
        "too_formal",
        "It still sounds too polished.",
        "local",
    )

    signal_row = _db(client).execute(
        """
        SELECT category, subject, signal, strength
        FROM preference_signals
        WHERE category = 'voice'
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    assert signal_row == ("voice", "formal_tone", "avoid", 4)

    voice_feedback_id = _db(client).execute(
        """
        SELECT id
        FROM voice_feedback
        WHERE query_feedback_id = ?
        """,
        (feedback_id,),
    ).fetchone()[0]
    evidence_rows = _db(client).execute(
        """
        SELECT er.kind, er.summary, el.target_type, el.target_id
        FROM evidence_records er
        JOIN evidence_links el ON el.evidence_id = er.id
        WHERE er.origin_table = 'voice_feedback' AND er.origin_id = ?
        ORDER BY el.target_type, el.target_id
        """,
        (voice_feedback_id,),
    ).fetchall()
    assert evidence_rows == [
        (
            "voice_feedback",
            "Local voice feedback marked the response as too_formal.",
            "query_feedback",
            feedback_id,
        ),
        (
            "voice_feedback",
            "Local voice feedback marked the response as too_formal.",
            "session",
            str(getattr(_app(client).state.current_session, "id")),
        ),
        (
            "voice_feedback",
            "Local voice feedback marked the response as too_formal.",
            "voice_feedback",
            voice_feedback_id,
        ),
    ]


def test_get_evidence_returns_privacy_safe_summaries_only(client: TestClient):
    response = client.post(
        "/query/feedback",
        json={
            "query": "How should I structure the morning?",
            "response": "Start with the hardest task first.",
            "feedback": "missed_context",
            "notes": "It ignored my existing calendar constraints.",
            "query_type": "simple",
            "backend_used": "local",
            "confidence": "medium_confidence",
            "intent": {
                "source_profile": "general",
                "intent_tags": ["planning"],
                "domain_hints": ["goals"],
            },
            "domains_referenced": ["goals"],
        },
        headers=_login_headers(client),
    )
    assert response.status_code == 200
    feedback_id = response.json()["id"]

    evidence_response = client.get(
        f"/evidence?target_type=query_feedback&target_id={feedback_id}",
        headers=_login_headers(client),
    )

    assert evidence_response.status_code == 200
    body = evidence_response.json()
    assert body["target_type"] == "query_feedback"
    assert body["target_id"] == feedback_id
    assert body["evidence"] == [
        {
            "kind": "query_feedback",
            "source_type": "user_feedback",
            "routing": "local_only",
            "summary": "Local query feedback marked as missed_context for a local response.",
            "source_ref": feedback_id,
            "metadata": {
                "backend": "local",
                "confidence": "medium_confidence",
                "query_type": "simple",
                "source_profile": "general",
            },
            "created_at": body["evidence"][0]["created_at"],
        }
    ]
    assert "How should I structure the morning?" not in evidence_response.text
    assert "Start with the hardest task first." not in evidence_response.text
    assert "It ignored my existing calendar constraints." not in evidence_response.text


def test_post_query_feedback_rejects_voice_feedback_for_non_voice_query(client: TestClient):
    response = client.post(
        "/query/feedback",
        json={
            "query": "What are my current goals?",
            "response": "Finish the current phase carefully.",
            "feedback": "helpful",
            "voice_feedback": "too_formal",
            "query_type": "simple",
            "backend_used": "local",
            "confidence": "medium_confidence",
            "intent": {
                "source_profile": "self_question",
                "intent_tags": ["self_model"],
                "domain_hints": ["goals"],
            },
            "domains_referenced": ["goals"],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "voice_feedback is only valid for voice_generation queries."


def test_query_stream_includes_blocked_privacy_state_on_error(client: TestClient, monkeypatch):
    _insert_attribute(
        _db(client),
        "fears",
        "fear_of_failure",
        "I worry about missing major deadlines.",
        routing="local_only",
    )
    # Bypass the insufficient_data short-circuit so the broker is always reached.
    monkeypatch.setattr(
        "server.routes.query._should_short_circuit_insufficient",
        lambda context: False,
    )
    monkeypatch.setattr(
        "server.routes.query.resolve_active_provider_config",
        lambda *args, **kwargs: ProviderConfig(
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
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RoutingViolationError("local_only attributes cannot be sent externally")
        ),
    )

    response = client.post(
        "/query/stream",
        json={"query": "What am I afraid of?", "backend_override": "external"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.text
    assert '"type": "error"' in body
    assert '"execution_mode": "blocked"' in body
    assert '"summary": "Blocked to protect local-only data from being sent externally."' in body


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
                        "routing_enforced": True,
                        "warning": "internal detail should stay server-side",
                        "reason": "local_only_context_blocked_for_external_inference",
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
    assert "query" not in session["routing_log"][0]
    assert session["routing_log"][0]["warning"] is None
    assert session["routing_log"][0]["reason"] is None
    assert session["routing_log"][0]["privacy"]["execution_mode"] == "external"
    assert session["privacy"]["execution_mode"] == "external"


def test_create_tables_scrubs_stored_query_text_from_reflection_sessions(monkeypatch):
    with get_plain_connection(":memory:") as conn:
        create_tables(conn)
        conn.execute(
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
                "1 query across session",
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
        conn.commit()

        create_tables(conn)

        stored = conn.execute(
            "SELECT routing_log FROM reflection_sessions"
        ).fetchone()
        assert stored is not None
        assert '"query"' not in str(stored[0])


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


def test_setup_model_options_returns_profiles_and_provider_statuses(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "server.routes.setup.get_provider_statuses",
        lambda conn: [
            SimpleNamespace(
                provider="ollama",
                label="Local model",
                configured=True,
                available=True,
                validated=True,
                is_local=True,
                model="llama3.1:8b",
                reason=None,
            ),
            SimpleNamespace(
                provider="anthropic",
                label="Anthropic",
                configured=False,
                available=False,
                validated=False,
                is_local=False,
                model="claude-sonnet-4-6",
                reason="API key not configured.",
            ),
        ],
    )

    response = client.get("/setup/model-options", headers=_login_headers(client))

    assert response.status_code == 200
    body = response.json()
    assert body["profiles"]
    assert body["providers"][0]["provider"] == "ollama"
    assert body["preferred_backend"] == "local"


def test_setup_profile_persists_backend_preference(client, monkeypatch):
    monkeypatch.setattr(
        "server.routes.setup.get_provider_statuses",
        lambda conn: [
            SimpleNamespace(
                provider="ollama",
                label="Local model",
                configured=True,
                available=True,
                validated=True,
                is_local=True,
                model="llama3.1:8b",
                reason=None,
            )
        ],
    )

    response = client.post(
        "/setup/profile",
        json={
            "profile": "private_local_first",
            "preferred_backend": "local",
            "onboarding_completed": True,
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    stored = _db(client).execute(
        "SELECT onboarding_completed, active_profile, preferred_backend FROM app_settings WHERE id = 1"
    ).fetchone()
    assert stored == (1, "private_local_first", "local")


def test_setup_provider_credentials_validates_and_saves(client, monkeypatch):
    saved = {}

    monkeypatch.setattr("server.routes.setup.set_api_key", lambda provider, api_key: saved.update({provider: api_key}))
    monkeypatch.setattr(
        "server.routes.setup.get_provider_statuses",
        lambda conn: [
            SimpleNamespace(
                provider="anthropic",
                label="Anthropic",
                configured=True,
                available=True,
                validated=True,
                is_local=False,
                model="claude-sonnet-4-6",
                reason=None,
            )
        ],
    )

    response = client.post(
        "/setup/providers/anthropic/credentials",
        json={"api_key": "sk-ant-valid-example"},  # pragma: allowlist secret
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert saved["anthropic"] == "sk-ant-valid-example"
    assert response.json()["available"] is True


def test_security_posture_route_returns_inspected_checks(client, monkeypatch):
    posture = {
        "platform": "macos",
        "supported": True,
        "checks": [
            {
                "code": "filevault",
                "label": "FileVault",
                "status": "enabled",
                "recommended_value": "Enabled with a personal recovery key stored locally.",
                "action_required": False,
                "user_marked_complete": False,
                "summary": "Enabled.",
                "recommendation": "Keep it on.",
            }
        ],
    }
    monkeypatch.setattr("server.routes.setup.resolve_security_posture", lambda conn: posture)

    response = client.get("/setup/security-posture", headers=_login_headers(client))

    assert response.status_code == 200
    assert response.json()["checks"][0]["status"] == "enabled"


def test_teach_bootstrap_returns_cards_and_questions(client, monkeypatch):
    posture = {
        "platform": "macos",
        "supported": True,
        "checks": [],
    }
    monkeypatch.setattr("server.routes.teach.resolve_security_posture", lambda conn: posture)
    monkeypatch.setattr(
        "server.routes.teach.get_provider_statuses",
        lambda conn: [
            SimpleNamespace(
                provider="ollama",
                label="Local model",
                configured=True,
                available=True,
                validated=True,
                is_local=True,
                model="llama3.1:8b",
                reason=None,
            )
        ],
    )

    response = client.get("/teach/bootstrap", headers=_login_headers(client))

    assert response.status_code == 200
    body = response.json()
    assert body["cards"][0]["type"] == "welcome"
    assert body["questions"]


def test_teach_bootstrap_includes_conversation_signal_card_when_staged_items_exist(client):
    _db(client).execute(
        """
        INSERT INTO extracted_session_signals (
            id, session_id, exchange_index, signal_type, payload_json, processed
        )
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (
            str(uuid.uuid4()),
            "session-1",
            0,
            "attribute_candidate",
            json.dumps(
                {
                    "domain": "goals",
                    "label": "career_direction",
                    "value": "I want to move toward technical leadership.",
                    "mutability": "evolving",
                    "confidence": 0.7,
                }
            ),
        ),
    )
    _db(client).commit()

    response = client.get("/teach/bootstrap", headers=_login_headers(client))

    assert response.status_code == 200
    cards = response.json()["cards"]
    conversation_cards = [card for card in cards if card["type"] == "conversation_signal"]
    assert conversation_cards
    assert conversation_cards[0]["payload"]["count"] == 1


def test_get_conversation_signals_returns_pending_staged_items(client):
    signal_id = str(uuid.uuid4())
    _db(client).execute(
        """
        INSERT INTO extracted_session_signals (
            id, session_id, exchange_index, signal_type, payload_json, processed
        )
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (
            signal_id,
            "session-2",
            1,
            "preference",
            json.dumps(
                {
                    "category": "work_style",
                    "subject": "solo_work",
                    "signal": "prefer",
                    "strength": 4,
                    "summary": "Recent conversations suggest a preference for solo work.",
                }
            ),
        ),
    )
    _db(client).commit()

    response = client.get("/teach/conversation-signals", headers=_login_headers(client))

    assert response.status_code == 200
    body = response.json()
    assert body["signals"]
    assert body["signals"][0]["id"] == signal_id
    assert body["signals"][0]["signal_type"] == "preference"


def test_accept_conversation_signal_promotes_attribute_candidate(client):
    signal_id = str(uuid.uuid4())
    _db(client).execute(
        """
        INSERT INTO extracted_session_signals (
            id, session_id, exchange_index, signal_type, payload_json, processed
        )
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (
            signal_id,
            "session-3",
            0,
            "attribute_candidate",
            json.dumps(
                {
                    "domain": "goals",
                    "label": "career_direction",
                    "value": "I want to move toward technical leadership.",
                    "elaboration": "This keeps coming up in planning questions.",
                    "mutability": "evolving",
                    "confidence": 0.7,
                }
            ),
        ),
    )
    _db(client).commit()

    response = client.post(
        f"/teach/conversation-signals/{signal_id}/accept",
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["attributes_saved"] == 1

    processed = _db(client).execute(
        "SELECT processed FROM extracted_session_signals WHERE id = ?",
        (signal_id,),
    ).fetchone()
    assert processed == (1,)

    stored = _db(client).execute(
        """
        SELECT label, value
        FROM attributes
        WHERE label = 'career_direction' AND status IN ('active', 'confirmed')
        """
    ).fetchone()
    assert stored == ("career_direction", "I want to move toward technical leadership.")


def test_dismiss_conversation_signal_marks_item_processed(client):
    signal_id = str(uuid.uuid4())
    _db(client).execute(
        """
        INSERT INTO extracted_session_signals (
            id, session_id, exchange_index, signal_type, payload_json, processed
        )
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (
            signal_id,
            "session-4",
            0,
            "correction",
            json.dumps(
                {
                    "summary": "The user corrected an overgeneralized pattern.",
                    "correction_text": "The pattern depends on context.",
                    "attribute_ids": [],
                }
            ),
        ),
    )
    _db(client).commit()

    response = client.post(
        f"/teach/conversation-signals/{signal_id}/dismiss",
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"
    processed = _db(client).execute(
        "SELECT processed FROM extracted_session_signals WHERE id = ?",
        (signal_id,),
    ).fetchone()
    assert processed == (1,)


def test_security_posture_override_persists_unknown_check_completion(client, monkeypatch):
    posture = {
        "platform": "macos",
        "supported": True,
        "checks": [
            {
                "code": "personal_recovery_key",
                "label": "Personal recovery key",
                "status": "unknown",
                "recommended_value": "Enabled.",
                "action_required": True,
                "summary": "A personal recovery key keeps recovery under your control.",
                "recommendation": "Prefer a personal recovery key.",
            }
        ],
    }
    monkeypatch.setattr("server.routes.setup.inspect_security_posture", lambda: posture)
    monkeypatch.setattr("engine.security_posture.inspect_security_posture", lambda: posture)

    response = client.post(
        "/setup/security-posture/checks/personal_recovery_key",
        json={"completed": True},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["checks"][0]["user_marked_complete"] is True
    assert body["checks"][0]["action_required"] is False

    stored = _db(client).execute(
        "SELECT is_complete FROM security_check_overrides WHERE check_code = ?",
        ("personal_recovery_key",),
    ).fetchone()
    assert stored == (1,)

    teach_response = client.get("/teach/bootstrap", headers=_login_headers(client))

    assert teach_response.status_code == 200
    assert teach_response.json()["security_posture"]["checks"][0]["user_marked_complete"] is True


def test_teach_answer_saves_attributes_and_marks_question_answered(client):
    question_id = str(uuid.uuid4())
    _db(client).execute(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage
        )
        VALUES (?, ?, ?, ?, 'catalog', 'pending', 10.0, 'teaching')
        """,
        (question_id, "What matters most to you at work?", "values", "values_priority"),
    )
    _db(client).commit()

    response = client.post(
        f"/teach/questions/{question_id}/answer",
        json={
            "answer": "Clear priorities.",
            "accepted": [
                {
                    "domain": "values",
                    "label": "work_priority",
                    "value": "I value clear priorities at work.",
                    "elaboration": None,
                    "mutability": "stable",
                    "confidence": 0.9,
                }
            ],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    stored = _db(client).execute(
        "SELECT status FROM teach_questions WHERE id = ?",
        (question_id,),
    ).fetchone()
    assert stored == ("answered",)


def test_teach_answer_response_does_not_repeat_answered_question(client):
    prompt = "What do you believe separates good engineers from great ones?"
    intent_key = "beliefs_what_do_you_believe_separates_good_engineers_from_great_ones"
    question_id = str(uuid.uuid4())
    _db(client).execute(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage
        )
        VALUES (?, ?, ?, ?, 'catalog', 'pending', 10.0, 'teaching')
        """,
        (question_id, prompt, "beliefs", intent_key),
    )
    _db(client).commit()

    response = client.post(
        f"/teach/questions/{question_id}/answer",
        json={
            "answer": "Strong engineers pair craft with judgment.",
            "accepted": [
                {
                    "domain": "beliefs",
                    "label": "engineering_judgment",
                    "value": "I think great engineers combine technical skill with judgment.",
                    "elaboration": None,
                    "mutability": "stable",
                    "confidence": 0.9,
                }
            ],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    stored = _db(client).execute(
        "SELECT status FROM teach_questions WHERE id = ?",
        (question_id,),
    ).fetchone()
    assert stored == ("answered",)
    next_questions = response.json()["next"]["questions"]
    assert next_questions
    assert all(item["intent_key"] != intent_key for item in next_questions)


def test_teach_answer_response_dismisses_legacy_duplicate_pending_question(client):
    prompt = "What do you believe about privacy in the modern world?"
    intent_key = "beliefs_what_do_you_believe_about_privacy_in_the_modern_world"
    question_id = str(uuid.uuid4())
    duplicate_id = str(uuid.uuid4())
    _db(client).executemany(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage
        )
        VALUES (?, ?, ?, ?, 'catalog', 'pending', 10.0, 'teaching')
        """,
        [
            (question_id, prompt, "beliefs", intent_key),
            (duplicate_id, prompt, "beliefs", intent_key),
        ],
    )
    _db(client).commit()

    response = client.post(
        f"/teach/questions/{question_id}/answer",
        json={
            "answer": "Privacy should preserve agency and dignity.",
            "accepted": [
                {
                    "domain": "beliefs",
                    "label": "privacy_agency",
                    "value": "I believe privacy protects agency and dignity.",
                    "elaboration": None,
                    "mutability": "stable",
                    "confidence": 0.9,
                }
            ],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    rows = _db(client).execute(
        "SELECT id, status FROM teach_questions WHERE intent_key = ? ORDER BY id",
        (intent_key,),
    ).fetchall()
    statuses = {str(row[0]): str(row[1]) for row in rows}
    assert statuses[question_id] == "answered"
    assert statuses[duplicate_id] == "dismissed"
    next_questions = response.json()["next"]["questions"]
    assert all(item["intent_key"] != intent_key for item in next_questions)


def test_teach_answer_response_dismisses_legacy_duplicate_pending_prompt(client):
    prompt = "What do you believe about privacy in the modern world?"
    question_id = str(uuid.uuid4())
    duplicate_id = str(uuid.uuid4())
    _db(client).executemany(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage
        )
        VALUES (?, ?, ?, ?, 'catalog', 'pending', 10.0, 'teaching')
        """,
        [
            (question_id, prompt, "beliefs", "beliefs_privacy_original"),
            (duplicate_id, prompt, "beliefs", "beliefs_privacy_duplicate"),
        ],
    )
    _db(client).commit()

    response = client.post(
        f"/teach/questions/{question_id}/answer",
        json={
            "answer": "Privacy should preserve agency and dignity.",
            "accepted": [
                {
                    "domain": "beliefs",
                    "label": "privacy_agency",
                    "value": "I believe privacy protects agency and dignity.",
                    "elaboration": None,
                    "mutability": "stable",
                    "confidence": 0.9,
                }
            ],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    rows = _db(client).execute(
        "SELECT id, status FROM teach_questions WHERE prompt = ? ORDER BY id",
        (prompt,),
    ).fetchall()
    statuses = {str(row[0]): str(row[1]) for row in rows}
    assert statuses[question_id] == "answered"
    assert statuses[duplicate_id] == "dismissed"
    next_questions = response.json()["next"]["questions"]
    assert all(item["prompt"] != prompt for item in next_questions)


def test_teach_answer_requires_consent_for_external_extraction(client, monkeypatch):
    question_id = str(uuid.uuid4())
    _db(client).execute(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage
        )
        VALUES (?, ?, ?, ?, 'catalog', 'pending', 10.0, 'teaching')
        """,
        (question_id, "What matters most to you at work?", "values", "values_priority"),
    )
    _db(client).commit()
    monkeypatch.setattr(
        "server.routes.teach.resolve_active_provider_config",
        lambda conn, default_config: _external_config(),
    )

    response = client.post(
        f"/teach/questions/{question_id}/answer",
        json={"answer": "Clear priorities."},
        headers=_login_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"] == "external_extraction_consent_required"


def test_teach_feedback_dismisses_question(client):
    question_id = str(uuid.uuid4())
    _db(client).execute(
        """
        INSERT INTO teach_questions (
            id, prompt, domain, intent_key, source, status, priority, onboarding_stage
        )
        VALUES (?, ?, ?, ?, 'catalog', 'pending', 10.0, 'teaching')
        """,
        (question_id, "What helps you focus?", "patterns", "patterns_focus"),
    )
    _db(client).commit()

    response = client.post(
        f"/teach/questions/{question_id}/feedback",
        json={"feedback": "duplicate"},
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    status = _db(client).execute(
        "SELECT status FROM teach_questions WHERE id = ?",
        (question_id,),
    ).fetchone()[0]
    assert status == "dismissed"


def test_post_artifacts_accepts_pdf_docx_and_tags(client):
    headers = _login_headers(client)

    pdf_response = client.post(
        "/artifacts",
        files={"file": ("notes.pdf", _simple_pdf_bytes("Planning roadmap"), "application/pdf")},
        data={"tags": '["roadmap","planning"]'},
        headers=headers,
    )
    assert pdf_response.status_code == 200

    docx_response = client.post(
        "/artifacts",
        files={
            "file": (
                "notes.docx",
                _simple_docx_bytes("Voice notes for onboarding"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=headers,
    )
    assert docx_response.status_code == 200

    tags = _db(client).execute(
        "SELECT tag FROM artifact_tags ORDER BY tag ASC"
    ).fetchall()
    assert [row[0] for row in tags] == ["planning", "roadmap"]


def test_post_artifacts_accepts_compressed_pdf_upload(client):
    response = client.post(
        "/artifacts",
        files={
            "file": (
                "compressed-notes.pdf",
                _compressed_pdf_bytes("Planning roadmap"),
                "application/pdf",
            )
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    artifact_id = response.json()["artifact_id"]
    row = _db(client).execute(
        "SELECT title, content FROM artifacts WHERE id = ?",
        (artifact_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "compressed-notes"
    assert "Planning roadmap" in row[1]


def test_post_artifact_analyze_enqueues_and_returns_202(client, monkeypatch):
    create_response = client.post(
        "/artifacts",
        json={
            "text": "Lasagna, tikka masala, and pasta bake are dinner recipes I have made.",
            "title": "Dinner recipes",
            "type": "document",
            "source": "upload",
        },
        headers=_login_headers(client),
    )
    artifact_id = create_response.json()["artifact_id"]

    monkeypatch.setattr(
        "server.routes.artifacts.resolve_local_provider_config",
        lambda *args, **kwargs: _config(),
    )

    response = client.post(
        f"/artifacts/{artifact_id}/analyze",
        headers=_login_headers(client),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["analysis_status"] == "queued"
    assert body["queued_at"] is not None
    assert body["artifact_id"] == artifact_id


def test_post_artifact_analyze_idempotent_when_already_queued(client, monkeypatch):
    create_response = client.post(
        "/artifacts",
        json={"text": "Some notes.", "title": "Notes"},
        headers=_login_headers(client),
    )
    artifact_id = create_response.json()["artifact_id"]
    monkeypatch.setattr(
        "server.routes.artifacts.resolve_local_provider_config",
        lambda *args, **kwargs: _config(),
    )

    client.post(f"/artifacts/{artifact_id}/analyze", headers=_login_headers(client))
    response = client.post(f"/artifacts/{artifact_id}/analyze", headers=_login_headers(client))

    body = response.json()
    assert body["analysis_status"] == "queued"


def test_post_artifact_analyze_returns_existing_when_already_analyzed(client, monkeypatch):
    create_response = client.post(
        "/artifacts",
        json={"text": "Some notes.", "title": "Notes"},
        headers=_login_headers(client),
    )
    artifact_id = create_response.json()["artifact_id"]

    conn = client.app.state.test_db
    analyzed_meta = {
        "analysis": {
            "status": "analyzed",
            "content_kind": "notes",
            "summary": "Pre-existing analysis.",
            "descriptor_tokens": ["notes"],
            "candidate_attributes": [],
            "candidate_preferences": [],
            "analyzed_at": "2026-04-20T12:00:00+00:00",
            "analysis_method": "model",
            "analysis_warning": None,
            "queued_at": "2026-04-20T11:59:00+00:00",
            "started_at": "2026-04-20T11:59:01+00:00",
            "completed_at": "2026-04-20T12:00:00+00:00",
        }
    }
    conn.execute(
        "UPDATE artifacts SET metadata = ? WHERE id = ?",
        (json.dumps(analyzed_meta), artifact_id),
    )
    conn.commit()

    response = client.post(f"/artifacts/{artifact_id}/analyze", headers=_login_headers(client))

    body = response.json()
    assert body["analysis_status"] == "analyzed"
    assert body["summary"] == "Pre-existing analysis."


def test_post_artifact_analyze_409_when_no_local_provider(client, monkeypatch):
    from config.llm_router import ConfigurationError

    create_response = client.post(
        "/artifacts",
        json={"text": "Some notes.", "title": "Notes"},
        headers=_login_headers(client),
    )
    artifact_id = create_response.json()["artifact_id"]

    monkeypatch.setattr(
        "server.routes.artifacts.resolve_local_provider_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConfigurationError("no local provider")),
    )

    response = client.post(
        f"/artifacts/{artifact_id}/analyze",
        headers=_login_headers(client),
    )

    assert response.status_code == 409


def test_post_artifact_promote_writes_selected_candidates(client):
    create_response = client.post(
        "/artifacts",
        json={
            "text": "Lasagna, tikka masala, and pasta bake are dinner recipes I have made.",
            "title": "Dinner recipes",
            "type": "document",
            "source": "upload",
        },
        headers=_login_headers(client),
    )
    artifact_id = create_response.json()["artifact_id"]
    metadata = {
        "analysis": {
            "status": "analyzed",
            "content_kind": "recipe_collection",
            "summary": "A local collection of dinner recipes.",
            "descriptor_tokens": ["recipe", "dinner", "meal"],
            "candidate_attributes": [
                {
                    "candidate_id": "attribute_0_dinner_recipes",
                    "domain": "patterns",
                    "label": "dinner_recipes",
                    "value": "The artifact tracks dinner recipes I have made.",
                    "elaboration": None,
                    "mutability": "evolving",
                    "confidence": 0.7,
                    "status": "pending",
                },
            ],
            "candidate_preferences": [
                {
                    "candidate_id": "preference_0_food_pasta",
                    "category": "food",
                    "subject": "pasta",
                    "signal": "like",
                    "strength": 3,
                    "summary": "Pasta appears repeatedly in the recipe list.",
                    "status": "pending",
                },
            ],
        }
    }
    _db(client).execute(
        "UPDATE artifacts SET metadata = ? WHERE id = ?",
        (json.dumps(metadata, sort_keys=True), artifact_id),
    )
    _db(client).commit()

    response = client.post(
        f"/artifacts/{artifact_id}/promote",
        json={
            "selected_attributes": [
                {
                    "candidate_id": "attribute_0_dinner_recipes",
                    "domain": "patterns",
                    "label": "dinner_recipes",
                    "value": "The artifact tracks dinner recipes I have made.",
                    "elaboration": None,
                    "mutability": "evolving",
                    "confidence": 0.7,
                    "status": "pending",
                },
            ],
            "selected_preferences": [
                {
                    "candidate_id": "preference_0_food_pasta",
                    "category": "food",
                    "subject": "pasta",
                    "signal": "like",
                    "strength": 3,
                    "summary": "Pasta appears repeatedly in the recipe list.",
                    "status": "pending",
                },
            ],
        },
        headers=_login_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["promoted_attribute_ids"]
    assert body["promoted_preference_signal_ids"]
    stored_attribute = _db(client).execute(
        "SELECT source, routing FROM attributes WHERE label = 'dinner_recipes'"
    ).fetchone()
    assert stored_attribute == ("explicit", "local_only")
    stored_signal = _db(client).execute(
        "SELECT category, subject, signal FROM preference_signals WHERE subject = 'pasta'"
    ).fetchone()
    assert stored_signal == ("food", "pasta", "like")
    assert body["analysis"]["candidate_attributes"][0]["status"] == "promoted"
    assert body["analysis"]["candidate_preferences"][0]["status"] == "promoted"
