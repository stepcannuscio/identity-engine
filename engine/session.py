"""State container for interactive identity query sessions."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

HISTORY_CAP = 6


class _RoutingLogSerializable(Protocol):
    """Typed interface for broker audit records written into routing logs."""

    def to_routing_log_entry(
        self,
        *,
        query_type: str | None = None,
    ) -> dict[str, object]: ...


@dataclass
class Session:
    """In-memory session state for one interactive query run."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
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

    def log_query(
        self,
        query_or_audit: str | _RoutingLogSerializable,
        query_type_or_audit: str | None = None,
        backend: str | None = None,
        attribute_count: int | None = None,
        domains_referenced: list[str] | None = None,
        *,
        query_type: str | None = None,
    ) -> None:
        """Record routing metadata for one query turn.

        New call sites should pass an ``InferenceDecision``. The legacy scalar
        signature is still supported to keep older tests and fixtures stable.
        """
        if not isinstance(query_or_audit, str):
            self.routing_log.append(
                query_or_audit.to_routing_log_entry(
                    query_type=query_type,
                )
            )
            return

        if query_type_or_audit is None:
            raise TypeError("Legacy session.log_query calls require a query_type string.")

        self.routing_log.append(
            {
                "query_type": query_type_or_audit,
                "backend": backend or "local",
                "attribute_count": attribute_count or 0,
                "domains_referenced": sorted(set(domains_referenced or [])),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    def to_db_record(self) -> dict:
        """Return a reflection_sessions-compatible record for this session."""
        external_calls = sum(
            1
            for entry in self.routing_log
            if entry.get("is_local") is False or entry.get("backend") != "local"
        )
        return {
            "session_type": "freeform",
            "summary": f"{self.query_count} queries across session",
            "attributes_created": 0,
            "attributes_updated": 0,
            "external_calls_made": external_calls,
            "routing_log": json.dumps(self.routing_log),
            "started_at": self.started_at,
            "ended_at": datetime.now(UTC),
        }


def write_session_record(conn, session: Session) -> None:
    """Persist one completed freeform session to ``reflection_sessions``."""
    record = session.to_db_record()
    started_at = record["started_at"]
    ended_at = record["ended_at"]
    if hasattr(started_at, "isoformat"):
        started_at = started_at.isoformat()
    if hasattr(ended_at, "isoformat"):
        ended_at = ended_at.isoformat()
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
