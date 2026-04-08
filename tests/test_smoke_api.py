"""Tests for scripts/smoke_api.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.smoke_api as smoke_api


def test_build_parser_defaults_timeout_to_120_seconds(monkeypatch):
    monkeypatch.delenv("SMOKE_API_TIMEOUT", raising=False)

    parser = smoke_api._build_parser()
    args = parser.parse_args([])

    assert args.timeout == 120.0


def test_build_parser_uses_env_timeout(monkeypatch):
    monkeypatch.setenv("SMOKE_API_TIMEOUT", "95")

    parser = smoke_api._build_parser()
    args = parser.parse_args([])

    assert args.timeout == 95.0


def test_request_uses_supplied_timeout():
    session = Mock()
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"status": "ok"}
    session.request.return_value = response

    body = smoke_api._request(
        session,
        "POST",
        "https://127.0.0.1:8443",
        "/capture",
        120.0,
        token="abc123",
        payload={"text": "hello"},
    )

    assert body == {"status": "ok"}
    session.request.assert_called_once_with(
        "POST",
        "https://127.0.0.1:8443/capture",
        headers={"Authorization": "Bearer abc123"},
        json={"text": "hello"},
        timeout=120.0,
    )
