"""Passphrase-based session authentication for the FastAPI server."""

from __future__ import annotations

import getpass
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request

from config.settings import get_ui_passphrase, set_ui_passphrase
from server.models.schemas import AuthStatus, LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_TTL = timedelta(hours=8)
LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILED_ATTEMPTS = 5
MAX_ACTIVE_SESSIONS = 5


def _utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_ui_passphrase_exists() -> None:
    """Prompt for and store the UI passphrase when it is missing."""
    if get_ui_passphrase():
        return

    while True:
        first = getpass.getpass("Set UI passphrase (min 12 chars): ")
        if len(first) < 12:
            print("Passphrase too short. Use at least 12 characters.")
            continue

        second = getpass.getpass("Confirm UI passphrase: ")
        if not secrets.compare_digest(first, second):
            print("Passphrases did not match. Try again.")
            continue

        set_ui_passphrase(first)
        print("UI passphrase stored in the system keychain.")
        return


def cleanup_expired_sessions(app) -> None:
    """Remove expired in-memory session tokens."""
    now = _utcnow()
    active = getattr(app.state, "active_sessions", {})
    expired = [token for token, expires_at in active.items() if expires_at <= now]
    for token in expired:
        active.pop(token, None)


def get_request_token(request: Request) -> str | None:
    """Extract the session token from supported request headers."""
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip() or None

    fallback = request.headers.get("X-Session-Token", "").strip()
    return fallback or None


def validate_session_token(app, token: str | None) -> datetime | None:
    """Validate a token against the in-memory session store."""
    if not token:
        return None

    cleanup_expired_sessions(app)
    expires_at = getattr(app.state, "active_sessions", {}).get(token)
    if expires_at is None:
        return None
    return expires_at if expires_at > _utcnow() else None


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


def _current_failures(app, ip: str) -> list[datetime]:
    now = _utcnow()
    attempts = getattr(app.state, "login_attempts", {}).get(ip, [])
    valid = [attempt for attempt in attempts if attempt > now - LOGIN_WINDOW]
    app.state.login_attempts[ip] = valid
    return valid


def _locked_until(app, ip: str) -> datetime | None:
    locked_until = getattr(app.state, "login_locks", {}).get(ip)
    if locked_until is None:
        return None
    if locked_until <= _utcnow():
        app.state.login_locks.pop(ip, None)
        return None
    return locked_until


def _record_failed_login(app, ip: str) -> None:
    failures = _current_failures(app, ip)
    failures.append(_utcnow())
    app.state.login_attempts[ip] = failures
    if len(failures) >= MAX_FAILED_ATTEMPTS:
        app.state.login_locks[ip] = _utcnow() + LOGIN_WINDOW


def _clear_login_failures(app, ip: str) -> None:
    app.state.login_attempts.pop(ip, None)
    app.state.login_locks.pop(ip, None)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request) -> LoginResponse:
    """Authenticate with the UI passphrase and mint a session token."""
    app = request.app
    ip = _client_ip(request)

    if _locked_until(app, ip) is not None:
        raise HTTPException(status_code=429, detail="too many login attempts")

    stored_passphrase = get_ui_passphrase()
    if not stored_passphrase or not secrets.compare_digest(stored_passphrase, payload.passphrase):
        _record_failed_login(app, ip)
        if _locked_until(app, ip) is not None:
            raise HTTPException(status_code=429, detail="too many login attempts")
        raise HTTPException(status_code=401, detail="invalid passphrase")

    cleanup_expired_sessions(app)
    if len(app.state.active_sessions) >= MAX_ACTIVE_SESSIONS:
        raise HTTPException(status_code=429, detail="maximum active sessions reached")

    token = secrets.token_hex(32)
    expires_at = _utcnow() + SESSION_TTL
    app.state.active_sessions[token] = expires_at
    _clear_login_failures(app, ip)
    return LoginResponse(token=token, expires_at=expires_at)


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    """Invalidate the current session token."""
    token = getattr(request.state, "auth_token", None)
    if token:
        request.app.state.active_sessions.pop(token, None)
    return {"status": "ok"}


@router.get("/status", response_model=AuthStatus)
def status(request: Request) -> AuthStatus:
    """Return the current authentication state for the active token."""
    return AuthStatus(
        authenticated=True,
        expires_at=getattr(request.state, "auth_expires_at", None),
    )
