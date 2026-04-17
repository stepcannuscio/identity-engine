"""FastAPI application setup and lifecycle for the identity-engine server."""

from __future__ import annotations

import ipaddress
import logging
import os
import stat
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import netifaces
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from config.llm_router import print_routing_report, resolve_router, shutdown_started_ollama
from config.settings import DB_DIR
from engine.prompt_builder import RoutingViolationError
from engine.session import Session
from server.auth import ensure_ui_passphrase_exists, router as auth_router
from server.db import get_db_connection
from server.middleware import SecurityMiddleware, apply_security_headers
from server.routes.attributes import RoutingProtectedError
from server.routes import (
    artifacts_router,
    attributes_router,
    capture_router,
    interview_router,
    preferences_router,
    query_router,
    session_router,
)

VERSION = "0.1.0"
PORT = 8443
CERT_DIR = DB_DIR / "certs"
KEY_PATH = CERT_DIR / "key.pem"
CERT_PATH = CERT_DIR / "cert.pem"
TAILSCALE_WARNING = (
    "WARNING: Tailscale not detected. Server bound to\n"
    "127.0.0.1 only. Mobile access will not be available.\n"
    "Install Tailscale for secure remote access."
)
logger = logging.getLogger(__name__)


def get_bind_ip() -> str:
    """Resolve the server bind IP without ever returning 0.0.0.0."""
    for interface in netifaces.interfaces():
        if interface != "tailscale0" and not interface.startswith("utun"):
            continue
        addresses = netifaces.ifaddresses(interface).get(netifaces.AF_INET, [])
        for address in addresses:
            ip = str(address.get("addr", ""))
            if ip.startswith("100."):
                return ip

    env_ip = os.getenv("IDENTITY_ENGINE_BIND_IP", "").strip()
    if env_ip:
        return env_ip

    print(TAILSCALE_WARNING)
    return "127.0.0.1"


def assert_safe_bind_ip(bind_ip: str) -> None:
    """Reject unsafe wildcard binding."""
    if bind_ip == "0.0.0.0":
        raise RuntimeError("Refusing to bind identity-engine server to 0.0.0.0.")


def _ensure_runtime_directory() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DB_DIR.chmod(0o700)
    except PermissionError:
        return


def ensure_tls_certs(bind_ip: str) -> tuple[Path, Path]:
    """Create self-signed TLS materials on first run."""
    _ensure_runtime_directory()
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CERT_DIR.chmod(0o700)
    except PermissionError:
        pass

    if KEY_PATH.exists() and CERT_PATH.exists():
        return KEY_PATH, CERT_PATH

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    now = datetime.now(UTC)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "identity-engine"),
            x509.NameAttribute(NameOID.COMMON_NAME, "identity-engine"),
        ]
    )
    san_values: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    try:
        san_values.append(x509.IPAddress(ipaddress.ip_address(bind_ip)))
    except ValueError:
        pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san_values), critical=False)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    CERT_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    return KEY_PATH, CERT_PATH


def _write_session_record(app: FastAPI) -> None:
    session = getattr(app.state, "current_session", None)
    if session is None:
        return

    record = session.to_db_record()
    started_at = record["started_at"]
    ended_at = record["ended_at"]
    if hasattr(started_at, "isoformat"):
        started_at = started_at.isoformat()
    if hasattr(ended_at, "isoformat"):
        ended_at = ended_at.isoformat()
    with get_db_connection() as conn:
        import uuid

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
                record["session_type"],
                record["summary"],
                record["attributes_created"],
                record["attributes_updated"],
                record["external_calls_made"],
                record["routing_log"],
                started_at,
                ended_at,
            ),
        )
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and tear down shared server resources."""
    bind_ip = get_bind_ip()
    assert_safe_bind_ip(bind_ip)
    ensure_tls_certs(bind_ip)
    llm_config = resolve_router()
    print_routing_report(llm_config)
    ensure_ui_passphrase_exists()

    app.state.bind_ip = bind_ip
    app.state.llm_config = llm_config
    app.state.active_sessions = {}
    app.state.login_attempts = {}
    app.state.login_locks = {}
    app.state.current_session = Session()

    print(f"Identity engine server ready at https://{bind_ip}:{PORT}")
    try:
        yield
    finally:
        app.state.active_sessions.clear()
        _write_session_record(app)
        shutdown_started_ollama()


def create_app() -> FastAPI:
    """Create the FastAPI application instance."""
    app = FastAPI(
        title="Identity Engine",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(SecurityMiddleware)
    app.include_router(auth_router)
    app.include_router(query_router)
    app.include_router(artifacts_router)
    app.include_router(attributes_router)
    app.include_router(capture_router)
    app.include_router(interview_router)
    app.include_router(preferences_router)
    app.include_router(session_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    @app.exception_handler(RoutingViolationError)
    async def routing_violation_handler(
        request: Request,
        exc: RoutingViolationError,
    ) -> Response:
        logger.warning("Routing violation on %s %s: %s", request.method, request.url.path, exc)
        response = JSONResponse({"error": "internal server error"}, status_code=500)
        return apply_security_headers(response)

    @app.exception_handler(RoutingProtectedError)
    async def routing_protected_handler(
        request: Request,
        exc: RoutingProtectedError,
    ) -> Response:
        logger.warning("Routing protected on %s %s: %s", request.method, request.url.path, exc)
        response = JSONResponse(
            {
                "error": "routing_protected",
                "message": (
                    "Attributes in this domain cannot be set to external_ok. "
                    "This is a privacy protection."
                ),
            },
            status_code=403,
        )
        return apply_security_headers(response)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> Response:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        response = JSONResponse({"error": "internal server error"}, status_code=500)
        return apply_security_headers(response)

    return app


app = create_app()
