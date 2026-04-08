"""Server-local database access helpers built on db.connection.get_connection."""

from __future__ import annotations

from contextlib import contextmanager

from db.connection import get_connection


@contextmanager
def get_db_connection():
    """Yield a fresh request-scoped database connection."""
    with get_connection() as conn:
        yield conn
