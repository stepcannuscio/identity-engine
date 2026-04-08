#!/usr/bin/env python3
"""Smoke-test helper for the identity-engine FastAPI backend."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any

import requests

DEFAULT_REQUEST_TIMEOUT_SECONDS = 120


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test the FastAPI backend.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "https://127.0.0.1:8443"),
        help="Server base URL. Defaults to BASE_URL or https://127.0.0.1:8443.",
    )
    parser.add_argument(
        "--passphrase",
        default=os.getenv("PASSPHRASE", ""),
        help="UI passphrase. Defaults to PASSPHRASE env var, otherwise prompts.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify TLS certificates instead of skipping verification.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("SMOKE_API_TIMEOUT", DEFAULT_REQUEST_TIMEOUT_SECONDS)),
        help=(
            "Per-request timeout in seconds. Defaults to SMOKE_API_TIMEOUT "
            f"or {DEFAULT_REQUEST_TIMEOUT_SECONDS}."
        ),
    )
    return parser


def _print_response(title: str, response: requests.Response) -> Any:
    print(f"\n==> {title}")
    print(f"HTTP {response.status_code}")
    response.raise_for_status()
    body = response.json()
    print(json.dumps(body, indent=2, sort_keys=True))
    return body


def _request(
    session: requests.Session,
    method: str,
    base_url: str,
    path: str,
    timeout: float,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = session.request(
        method,
        f"{base_url}{path}",
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    return _print_response(f"{method} {path}", response)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    passphrase = args.passphrase or getpass.getpass("UI passphrase: ")

    session = requests.Session()
    session.verify = args.verify

    if not args.verify:
        requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

    try:
        _request(session, "GET", args.base_url, "/health", args.timeout)

        login_body = _request(
            session,
            "POST",
            args.base_url,
            "/auth/login",
            args.timeout,
            payload={"passphrase": passphrase},
        )
        token = login_body.get("token")
        if not isinstance(token, str) or not token:
            print("Login failed: token missing from response.", file=sys.stderr)
            return 1

        _request(session, "GET", args.base_url, "/auth/status", args.timeout, token=token)
        _request(session, "GET", args.base_url, "/attributes", args.timeout, token=token)
        _request(session, "GET", args.base_url, "/domains", args.timeout, token=token)
        _request(
            session,
            "POST",
            args.base_url,
            "/capture/preview",
            args.timeout,
            token=token,
            payload={
                "text": "I focus best in the morning.",
                "domain_hint": "patterns",
            },
        )
        _request(
            session,
            "POST",
            args.base_url,
            "/capture",
            args.timeout,
            token=token,
            payload={
                "text": "I focus best in the morning.",
                "domain_hint": "patterns",
            },
        )
        _request(
            session,
            "POST",
            args.base_url,
            "/attributes",
            args.timeout,
            token=token,
            payload={
                "domain": "goals",
                "label": "smoke_test_goal",
                "value": "I want the backend smoke test to pass.",
                "elaboration": None,
                "mutability": "evolving",
                "source": "explicit",
                "confidence": 0.8,
                "routing": "local_only",
            },
        )
        _request(
            session,
            "POST",
            args.base_url,
            "/query",
            args.timeout,
            token=token,
            payload={
                "query": "What are my current goals?",
                "backend_override": None,
            },
        )
        _request(session, "GET", args.base_url, "/sessions", args.timeout, token=token)
        _request(session, "GET", args.base_url, "/sessions/current", args.timeout, token=token)
        _request(session, "POST", args.base_url, "/auth/logout", args.timeout, token=token)
    except requests.HTTPError as exc:
        print(f"\nRequest failed: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"\nConnection failed: {exc}", file=sys.stderr)
        return 1

    print("\nSmoke test complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
