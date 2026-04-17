#!/usr/bin/env python3
"""
seed_interview.py — Interactive terminal interview that extracts structured
identity attributes and writes confirmed entries to the identity-engine database.

Run via:  make interview
          .venv/bin/python scripts/seed_interview.py
"""

import sys
import os
import uuid
import datetime
import threading
import time

# Project root must be on the path so db/ and config/ are importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.connection import get_connection
from config.llm_router import (
    resolve_router,
    print_routing_report,
    ConfigurationError,
    ExtractionError,
    ProviderConfig,
)
from engine.interview_capture import find_existing_active, get_domain_id, write_attribute
from engine.interview_catalog import DOMAINS
from engine.privacy_broker import PrivacyBroker

# ---------------------------------------------------------------------------
# Database check
# ---------------------------------------------------------------------------


def check_database() -> None:
    """Verify the encrypted database is accessible."""
    try:
        with get_connection() as conn:
            conn.execute("SELECT count(*) FROM domains;")
    except RuntimeError as exc:
        print(f"\nError: {exc}")
        print("Run 'make init' to initialise the database first.")
        sys.exit(1)
    except Exception as exc:
        print(f"\nDatabase error: {exc}")
        print("Run 'make init' to initialise the database first.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Domain selection
# ---------------------------------------------------------------------------

def select_domains() -> list:
    """Return a list of 0-based domain indices chosen by the user."""
    print("\nDomains available:")
    for i, domain in enumerate(DOMAINS, 1):
        print(f"  {i}. {str(domain['name']).capitalize()} — {domain['description']}")
    print()
    print("Would you like to go through all domains, or focus on specific ones?")
    print("Enter 'all' or a comma-separated list of numbers (e.g. '1,3,5'):")

    while True:
        try:
            choice = input("> ").strip().lower()
        except EOFError:
            sys.exit(0)

        if choice == "all":
            return list(range(len(DOMAINS)))

        try:
            indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip()]
            if indices and all(0 <= idx < len(DOMAINS) for idx in indices):
                return indices
            print(f"  Please enter numbers between 1 and {len(DOMAINS)}.")
        except ValueError:
            print("  Enter 'all' or numbers like '1,3,5'.")


# ---------------------------------------------------------------------------
# Elapsed-time spinner
# ---------------------------------------------------------------------------

def _run_with_elapsed(label: str, fn):
    """Run fn() in a background thread, printing elapsed seconds while it runs."""
    holder: dict = {}

    def _worker():
        try:
            holder["result"] = fn()
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    max_width = 0
    start = time.time()
    while thread.is_alive():
        elapsed = int(time.time() - start)
        msg = f"\r{label} {elapsed}s"
        max_width = max(max_width, len(msg))
        print(msg, end="", flush=True)
        thread.join(timeout=1.0)

    print(f"\r{' ' * max_width}\r", end="", flush=True)

    if "error" in holder:
        raise holder["error"]
    return holder["result"]


# ---------------------------------------------------------------------------
# Preview and confirmation UI
# ---------------------------------------------------------------------------

def display_preview(attributes: list) -> None:
    print("\n--- Preview ---")
    for i, attr in enumerate(attributes, 1):
        mutability = attr.get("mutability", "?")
        confidence = attr.get("confidence", 0.0)
        elaboration = attr.get("elaboration")
        print(f"[{i}] {attr['label']} ({mutability}, confidence: {confidence:.2f})")
        print(f"    Value: \"{attr['value']}\"")
        if elaboration:
            print(f"    Elaboration: \"{elaboration}\"")
    print()
    print("Options:")
    print("  Press Enter to confirm all")
    print("  Type numbers to skip specific ones (e.g. '2,3')")
    print("  Type 'e<N>' to edit a value (e.g. 'e1')")
    print("  Type 'r' to rephrase your answer and retry")
    print("  Type 's' to skip this question entirely")


def confirm_attributes(attributes: list) -> tuple:
    """Interactive confirmation loop.

    Returns:
        (confirmed_list, retry)
        - confirmed_list is None  → user chose to skip the question entirely
        - confirmed_list is []    → all attributes were individually skipped
        - retry is True           → user wants to rephrase and re-extract
    """
    while True:
        display_preview(attributes)
        try:
            choice = input("> ").strip()
        except EOFError:
            return None, False

        if choice == "":
            return list(attributes), False

        if choice.lower() == "s":
            return None, False

        if choice.lower() == "r":
            return [], True

        if choice.lower().startswith("e") and len(choice) > 1:
            try:
                idx = int(choice[1:]) - 1
            except ValueError:
                print("  Invalid input. Try 'e1' to edit attribute 1.")
                continue
            if not (0 <= idx < len(attributes)):
                print(f"  Invalid index. Enter a number between 1 and {len(attributes)}.")
                continue
            attr = attributes[idx]
            print(f"\nCurrent value for '{attr['label']}':")
            print(f"  \"{attr['value']}\"")
            print("Enter replacement (or press Enter to keep current):")
            try:
                new_val = input("> ").strip()
            except EOFError:
                continue
            if new_val:
                attr["value"] = new_val
            continue

        try:
            skip_set = {int(x.strip()) for x in choice.split(",") if x.strip()}
            confirmed = [a for i, a in enumerate(attributes, 1) if i not in skip_set]
            return confirmed, False
        except ValueError:
            print(
                "  Unrecognised input. Press Enter to confirm all, "
                "or type numbers like '2,3' to skip."
            )


def write_reflection_session(
    conn,
    started_at: datetime.datetime,
    domains_covered: list,
    attributes_created: int,
    attributes_updated: int,
) -> None:
    domain_list = ", ".join(domains_covered) if domains_covered else "none"
    summary = f"Guided interview covering: {domain_list}"
    conn.execute(
        "INSERT INTO reflection_sessions "
        "(id, session_type, summary, attributes_created, attributes_updated, "
        "external_calls_made, started_at, ended_at) "
        "VALUES (?, 'guided', ?, ?, ?, 0, ?, ?)",
        (
            str(uuid.uuid4()),
            summary,
            attributes_created,
            attributes_updated,
            started_at.isoformat(),
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Per-question interview step
# ---------------------------------------------------------------------------

def interview_question(question: str, domain_name: str, config: ProviderConfig) -> tuple:
    """Run the full ask → extract → preview → confirm → write cycle for one question.

    Returns (created_count, updated_count).
    """
    created = 0
    updated = 0

    print(f"\n{question}")

    while True:
        try:
            answer = input("\nYour answer: ").strip()
        except EOFError:
            return created, updated

        if not answer:
            print("  (No answer given — skipping.)")
            return created, updated

        print()
        try:
            attributes = _run_with_elapsed(
                "Extracting attributes...",
                lambda: PrivacyBroker(config).extract_interview_attributes(
                    question,
                    answer,
                ).content,
            )
        except ExtractionError as exc:
            print(f"Could not parse a valid response: {exc}")
            print("Would you like to rephrase your answer and retry? (y/n)")
            if input("> ").strip().lower() == "y":
                continue
            return created, updated
        except Exception as exc:
            print(f"Error: {exc}")
            print("Would you like to retry? (y/n)")
            if input("> ").strip().lower() == "y":
                continue
            return created, updated

        if not attributes:
            print("No attributes extracted.")
            return created, updated

        confirmed, retry = confirm_attributes(attributes)

        if retry:
            continue

        if confirmed is None:
            print("  (Question skipped.)")
            return created, updated

        if not confirmed:
            print("  (All attributes skipped.)")
            return created, updated

        with get_connection() as conn:
            domain_id = get_domain_id(conn, domain_name)
            for attr in confirmed:
                existing = find_existing_active(conn, domain_id, attr["label"])
                if existing:
                    _, existing_value, _ = existing
                    print(f"\n  Existing attribute '{attr['label']}' found:")
                    print(f"  Current value: \"{existing_value}\"")
                    print("  Update it? (y/n)")
                    try:
                        upd = input("  > ").strip().lower()
                    except EOFError:
                        upd = "n"
                    if upd != "y":
                        print(f"  Skipped: {attr['label']}")
                        continue

                outcome = write_attribute(conn, domain_id, attr, existing)
                if outcome == "updated":
                    updated += 1
                    print(f"  Updated: {attr['label']}")
                else:
                    created += 1
                    print(f"  Saved: {attr['label']}")

        return created, updated


# ---------------------------------------------------------------------------
# Main interview loop
# ---------------------------------------------------------------------------

def run_interview(config: ProviderConfig) -> None:
    started_at = datetime.datetime.now(datetime.UTC)
    total_created = 0
    total_updated = 0
    domains_covered: list = []

    domain_indices = select_domains()
    selected = [DOMAINS[i] for i in domain_indices]

    print(f"\nStarting interview — {len(selected)} domain(s) selected.")
    print("Answer each question in your own words. Take your time.")
    print("Nothing is written without your confirmation. Ctrl+C saves and exits.\n")

    try:
        for d_idx, domain in enumerate(selected):
            domain_name = domain["name"]
            print(f"\n{'=' * 60}")
            print(f"DOMAIN: {domain_name.upper()}")
            print(f"{domain['description']}")
            print(f"{'=' * 60}")

            domain_created = 0
            domain_updated = 0

            for question in domain["questions"]:
                q_created, q_updated = interview_question(question, domain_name, config)
                domain_created += q_created
                domain_updated += q_updated

            total_created += domain_created
            total_updated += domain_updated
            if domain_name not in domains_covered:
                domains_covered.append(domain_name)

            n_saved = domain_created + domain_updated
            print(f"\nDomain complete: {n_saved} attribute(s) saved.")

            if d_idx < len(selected) - 1:
                print("Continue to next domain? (y/n/q to quit)")
                try:
                    cont = input("> ").strip().lower()
                except EOFError:
                    cont = "q"
                if cont in ("n", "q"):
                    break

    except KeyboardInterrupt:
        print("\n\nInterrupted. Saving session record...")

    print(f"\n{'=' * 60}")
    print("Session complete.")
    print(f"  Attributes saved (new): {total_created}")
    print(f"  Attributes updated:     {total_updated}")
    print(f"  Domains covered:        {', '.join(domains_covered) or 'none'}")

    try:
        with get_connection() as conn:
            write_reflection_session(
                conn, started_at, domains_covered, total_created, total_updated
            )
        print("Session saved. Run 'make view' to view your data.")
    except Exception as exc:
        print(f"Warning: Could not save session record: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("identity-engine — Identity Interview")
    print("━" * 40)

    try:
        config = resolve_router()
    except ConfigurationError as exc:
        print(f"\nConfiguration error: {exc}")
        sys.exit(1)

    print_routing_report(config)

    print("Checking database...", end=" ", flush=True)
    check_database()
    print("OK")

    print()
    print("This session will guide you through questions across your selected identity domains.")
    print("Nothing is written to the database without your explicit confirmation.")

    run_interview(config)


if __name__ == "__main__":
    main()
