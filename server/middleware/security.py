"""Security middleware for interface binding, auth enforcement, and access logs."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import DB_DIR
from server.auth import get_request_token, validate_session_token

ACCESS_LOG_PATH = DB_DIR / "access.log"
_PUBLIC_PATHS = {"/health", "/auth/login"}


def _ensure_log_directory() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DB_DIR.chmod(0o700)
    except PermissionError:
        return


def _write_access_log(method: str, path: str, status: int, duration_ms: int) -> None:
    _ensure_log_directory()
    timestamp = datetime.now(UTC).isoformat()
    line = f"{timestamp} | {method} | {path} | {status} | {duration_ms}\n"
    try:
        with ACCESS_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except PermissionError:
        return


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin == f"{request.url.scheme}://{request.url.netloc}"


def _origin_value(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin and _same_origin(request):
        return origin
    return None


def _request_matches_bind_interface(request: Request) -> bool:
    expected = getattr(request.app.state, "bind_ip", None)
    server = request.scope.get("server")
    actual = server[0] if isinstance(server, tuple) and server else None

    if actual in {None, "testserver"}:
        return True
    if expected in {"127.0.0.1", "localhost"} and actual in {"127.0.0.1", "localhost"}:
        return True
    return expected == actual


def apply_security_headers(response: Response, origin: str | None = None) -> Response:
    """Attach the required security headers to a response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response


class SecurityMiddleware(BaseHTTPMiddleware):
    """Enforce interface binding, same-origin access, auth, and access logging."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        started = time.monotonic()
        origin = _origin_value(request)

        if not _same_origin(request):
            response: Response = JSONResponse({"error": "origin not allowed"}, status_code=403)
            response = apply_security_headers(response)
            _write_access_log(
                request.method,
                request.url.path,
                response.status_code,
                int((time.monotonic() - started) * 1000),
            )
            return response

        if request.method == "OPTIONS":
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, X-Session-Token, Content-Type"
            )
            response = apply_security_headers(response, origin=origin)
            _write_access_log(
                request.method,
                request.url.path,
                response.status_code,
                int((time.monotonic() - started) * 1000),
            )
            return response

        if not _request_matches_bind_interface(request):
            response = JSONResponse({"error": "invalid interface"}, status_code=403)
            response = apply_security_headers(response, origin=origin)
            _write_access_log(
                request.method,
                request.url.path,
                response.status_code,
                int((time.monotonic() - started) * 1000),
            )
            return response

        if request.url.path not in _PUBLIC_PATHS:
            token = get_request_token(request)
            expires_at = validate_session_token(request.app, token)
            if expires_at is None:
                response = JSONResponse(
                    {"error": "authentication required"},
                    status_code=401,
                )
                response = apply_security_headers(response, origin=origin)
                _write_access_log(
                    request.method,
                    request.url.path,
                    response.status_code,
                    int((time.monotonic() - started) * 1000),
                )
                return response
            request.state.auth_token = token
            request.state.auth_expires_at = expires_at

        response = await call_next(request)
        response = apply_security_headers(response, origin=origin)
        _write_access_log(
            request.method,
            request.url.path,
            response.status_code,
            int((time.monotonic() - started) * 1000),
        )
        return response
