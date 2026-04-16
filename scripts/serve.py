#!/usr/bin/env python3
"""HTTPS server entrypoint for the identity-engine web app.

Starts the FastAPI backend over HTTPS and mounts the built React frontend from
frontend/dist when that production bundle exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.main import app, assert_safe_bind_ip, ensure_tls_certs, get_bind_ip


DIST_PATH = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if DIST_PATH.exists():
    app.mount("/", StaticFiles(directory=str(DIST_PATH), html=True), name="static")


if __name__ == "__main__":
    bind_ip = get_bind_ip()
    assert_safe_bind_ip(bind_ip)
    key_path, cert_path = ensure_tls_certs(bind_ip)
    uvicorn.run(
        app,
        host=bind_ip,
        port=8443,
        ssl_keyfile=str(key_path.expanduser()),
        ssl_certfile=str(cert_path.expanduser()),
        reload=False,
        log_level="warning",
    )
