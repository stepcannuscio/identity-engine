"""Identity query engine package.

Provides fast query classification, attribute retrieval, prompt construction,
stateful interactive sessions, and the high-level query orchestration function.
"""

from engine.query_engine import query
from engine.session import Session

__all__ = ["query", "Session"]
