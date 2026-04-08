#!/usr/bin/env python3
"""CLI entry point for quick identity capture."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.llm_router import ConfigurationError, print_routing_report, resolve_router
from db.connection import get_connection
from engine.capture import capture


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quick-capture identity attributes.")
    parser.add_argument("positional_text", nargs="?", help="Quick capture text.")
    parser.add_argument("--text", dest="flag_text", help="Quick capture text.")
    parser.add_argument("--domain", default=os.environ.get("DOMAIN"), help="Optional domain hint.")
    return parser


def _resolve_text(args: argparse.Namespace) -> str:
    text = args.flag_text or args.positional_text or os.environ.get("TEXT")
    if not text:
        raise ValueError("TEXT is required. Pass it as a positional argument, --text, or TEXT env.")
    return str(text)


def main() -> None:
    parser = _build_parser()
    try:
        args = parser.parse_args()
        text = _resolve_text(args)
        config = resolve_router()
    except ValueError as exc:
        parser.error(str(exc))
    except ConfigurationError as exc:
        print(exc)
        sys.exit(1)

    print_routing_report(config)

    try:
        with get_connection() as conn:
            saved = capture(text, args.domain, conn, config)
            print(f"Capture complete: {len(saved)} attribute(s) saved.")
    except ValueError as exc:
        print(exc)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Capture cancelled.")


if __name__ == "__main__":
    main()
