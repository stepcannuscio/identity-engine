"""State container for interactive identity query sessions."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

HISTORY_CAP = 6


@dataclass
class Session:
    """In-memory session state for one interactive query run."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=datetime.now)
    history: list[dict] = field(default_factory=list)
    query_count: int = 0
    attributes_retrieved: int = 0
    routing_log: list[dict] = field(default_factory=list)

    def add_exchange(self, query: str, response: str) -> None:
        """Append a user/assistant pair and enforce the 6-exchange history cap."""
        self.history.append({"role": "user", "content": query})
        self.history.append({"role": "assistant", "content": response})

        max_messages = HISTORY_CAP * 2
        while len(self.history) > max_messages:
            self.history = self.history[2:]

    def get_history(self) -> list[dict]:
        """Return the current session history as a chat messages array."""
        return list(self.history)

    def log_query(self, query: str, query_type: str, backend: str, attribute_count: int) -> None:
        """Record routing metadata for one query turn."""
        self.routing_log.append(
            {
                "query": query,
                "query_type": query_type,
                "backend": backend,
                "attribute_count": attribute_count,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def to_db_record(self) -> dict:
        """Return a reflection_sessions-compatible record for this session."""
        external_calls = sum(1 for entry in self.routing_log if entry["backend"] != "local")
        return {
            "session_type": "freeform",
            "summary": f"{self.query_count} queries across session",
            "attributes_created": 0,
            "attributes_updated": 0,
            "external_calls_made": external_calls,
            "routing_log": json.dumps(self.routing_log),
            "started_at": self.started_at,
            "ended_at": datetime.now(),
        }
