#!/usr/bin/env python3
"""Interactive freeform query session for the identity engine."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow direct script execution from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import ConfigurationError, print_routing_report, resolve_router
from db.connection import get_connection
from engine.query_engine import query
from engine.session import Session, write_session_record


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
            write_session_record(conn, session)
            print(f"Session summary: {session.query_count} queries made, backend={backend}.")


if __name__ == "__main__":
    main()
