#!/usr/bin/env python3
"""Interactive freeform query session for the identity engine."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Allow direct script execution from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import ConfigurationError, print_routing_report, resolve_router
from db.connection import get_connection
from engine.query_engine import query
from engine.session import Session


def _print_history(history: list[dict]) -> None:
    if not history:
        print("(history is empty)")
        return

    print("\nSession history:")
    for msg in history:
        role = str(msg.get("role", "unknown")).capitalize()
        content = str(msg.get("content", ""))
        print(f"{role}: {content}")


def _print_status(session: Session, backend: str) -> None:
    print(
        f"queries={session.query_count} "
        f"attributes_retrieved={session.attributes_retrieved} "
        f"backend={backend}"
    )


def _write_session_record(conn, session: Session) -> None:
    record = session.to_db_record()
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
            record["started_at"],
            record["ended_at"],
        ),
    )
    conn.commit()


def main() -> None:
    try:
        config = resolve_router()
    except ConfigurationError as exc:
        print(exc)
        sys.exit(1)

    print_routing_report(config)

    backend = "local" if config.is_local else str(config.provider)
    session = Session()

    with get_connection() as conn:
        print("Identity engine ready. Type your question, or 'quit' to exit.")

        try:
            while True:
                try:
                    user_input = input("You: ")
                except EOFError:
                    break

                prompt = user_input.strip()
                lowered = prompt.lower()

                if lowered in {"quit", "q"}:
                    break

                if lowered == "history":
                    _print_history(session.get_history())
                    continue

                if lowered == "clear":
                    session.history = []
                    print("History cleared.")
                    continue

                if lowered == "status":
                    _print_status(session, backend)
                    continue

                if prompt == "":
                    continue

                response = query(prompt, session, conn, config)
                print(response)

        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            _write_session_record(conn, session)
            print(f"Session summary: {session.query_count} queries made, backend={backend}.")


if __name__ == "__main__":
    main()
