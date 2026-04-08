#!/usr/bin/env python3
"""
seed_interview.py — Interactive terminal interview that extracts structured
identity attributes via Ollama and writes confirmed entries to the
identity-engine database.

Run via:  make interview
          .venv/bin/python scripts/seed_interview.py
"""

import sys
import os
import json
import uuid
import datetime
import subprocess
import threading
import time

# Project root must be on the path so db/ and config/ are importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from db.connection import get_connection
from config.settings import LOCAL_ONLY, REFLECTION, STABLE, EVOLVING, DB_DIR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_TIMEOUT = 120
OLLAMA_LOG_PATH = DB_DIR / "ollama.log"

EXTRACT_SYSTEM_PROMPT = (
    "You are a structured data extractor for a personal identity store. "
    "Given the user's answer to an identity question, extract one or more identity attributes. "
    "For each attribute output a JSON object with these exact fields:\n"
    "- label: short snake_case identifier (e.g. 'recharge_style')\n"
    "- value: a clear, specific description in first person where natural (1-3 sentences max)\n"
    "- elaboration: any nuance or context worth preserving, or null\n"
    "- mutability: 'stable' or 'evolving'\n"
    "- confidence: float between 0.0 and 1.0\n\n"
    "Return a JSON array of attribute objects. Return JSON only. "
    "No preamble, no explanation, no markdown fences."
)

# ---------------------------------------------------------------------------
# Domain definitions — questions must remain in this exact order
# ---------------------------------------------------------------------------

DOMAINS = [
    {
        "name": "personality",
        "description": "Core personality traits, thinking styles, and behavioral defaults.",
        "questions": [
            "How do you recharge after a demanding day or week?",
            "Walk me through how you typically make an important decision.",
            "How do you respond when you don't have enough information to act?",
            "What does conflict look like for you — how do you handle it?",
            "Describe your ideal working conditions.",
            "How do you respond to critical feedback?",
            "What kind of work puts you in a state of flow most reliably?",
        ],
    },
    {
        "name": "values",
        "description": "Deeply held values, ethical commitments, and non-negotiables.",
        "questions": [
            "What are the two or three things you would never compromise on?",
            "What does integrity mean to you in day-to-day terms?",
            "How do you think about money — what role does it play in your life?",
            "What does a life well-lived look like to you?",
        ],
    },
    {
        "name": "goals",
        "description": "Short-term and long-term goals, aspirations, and active letting-go.",
        "questions": [
            "What is the most important thing you are trying to achieve in the next six months, professionally?",
            "What is the most important thing you are trying to achieve in the next six months, personally?",
            "What does success look like to you right now — not abstractly, but concretely?",
            "What are you actively trying to stop doing or let go of?",
        ],
    },
    {
        "name": "patterns",
        "description": "Recurring behavioral patterns, habits, and tendencies.",
        "questions": [
            "When are you most productive during the day, and what does that look like?",
            "What does procrastination look like for you specifically?",
            "How do you behave when you are under significant stress?",
            "What pulls you off track most reliably?",
            "How do you learn new things best?",
        ],
    },
    {
        "name": "voice",
        "description": "Communication style, tone, and self-expression.",
        "questions": [
            "How would you describe your communication style to someone who has never met you?",
            "How does the way you write or speak change between professional and personal contexts?",
            "What tone do you default to when you are most yourself?",
        ],
    },
    {
        "name": "relationships",
        "description": "Attitudes, needs, and patterns around relationships.",
        "questions": [
            "What do you need most from close friendships?",
            "How do you show care for people you are close to?",
            "What causes you to pull back from someone?",
            "How is trust built and broken for you?",
        ],
    },
    {
        "name": "fears",
        "description": "Fears, anxieties, and avoidance patterns.",
        "questions": [
            "What does professional failure look like in your head?",
            "What are you most afraid staying the same would mean?",
            "What do you most not want people to think about you?",
        ],
    },
    {
        "name": "beliefs",
        "description": "Beliefs about the world, work, and self.",
        "questions": [
            "What do you believe separates good engineers from great ones?",
            "How do you think about the role of luck versus effort in outcomes?",
            "What do you believe about privacy in the modern world?",
            "Where do you think software engineering is headed, and what does that mean for you?",
        ],
    },
]

# ---------------------------------------------------------------------------
# Ollama server management
# ---------------------------------------------------------------------------

def _ollama_is_running() -> bool:
    """Return True if Ollama is already listening on port 11434."""
    try:
        requests.get(OLLAMA_BASE_URL, timeout=2)
        return True
    except Exception:
        return False


def ensure_ollama():
    """Ensure Ollama is running, starting it if necessary.

    Returns the Popen process if this call started it, or None if it was
    already running. The caller is responsible for terminating the process
    on exit.
    """
    if _ollama_is_running():
        return None

    # Start Ollama, redirecting its output to a log file.
    OLLAMA_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(OLLAMA_LOG_PATH, "a") as log_fh:
            process = subprocess.Popen(
                ["ollama", "serve"],
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True
            )
    except FileNotFoundError:
        print("\nError: 'ollama' command not found.")
        print("Install Ollama from https://ollama.com and retry.")
        sys.exit(1)

    # Poll for up to 10 seconds.
    deadline = time.time() + 10
    while time.time() < deadline:
        if _ollama_is_running():
            return process
        time.sleep(0.5)

    process.terminate()
    print(f"\nError: Ollama did not become available within 10 seconds.")
    print(f"Check {OLLAMA_LOG_PATH} for details.")
    sys.exit(1)


def ensure_model() -> None:
    """Check that llama3.1:8b is available; pull it if not."""
    resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
    resp.raise_for_status()
    models = [m.get("name", "") for m in resp.json().get("models", [])]
    if not any(m.startswith("llama3.1:8b") for m in models):
        print("llama3.1:8b not found. Pulling now — this may take a few minutes on first run...")
        subprocess.run(["ollama", "pull", "llama3.1:8b"])


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
        print(f"  {i}. {domain['name'].capitalize()} — {domain['description']}")
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
# Ollama interaction
# ---------------------------------------------------------------------------

def _run_with_elapsed(label: str, fn):
    """Run fn() in a background thread, printing elapsed seconds in place while it runs.

    Clears the status line before returning or re-raising any exception fn() raised.
    """
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

    # Erase the status line completely before returning.
    print(f"\r{' ' * max_width}\r", end="", flush=True)

    if "error" in holder:
        raise holder["error"]
    return holder["result"]


def call_ollama(question: str, answer: str) -> str:
    """Send question + answer to Ollama and return the raw content string."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Question: {question}\n\nAnswer: {answer}"},
        ],
        "stream": False,
    }
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def parse_attributes(raw: str) -> list:
    """Strip any markdown fences and parse JSON from Ollama output."""
    content = raw
    if content.startswith("```"):
        lines = content.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        content = "\n".join(lines).strip()
    return json.loads(content)


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
    """
    Interactive confirmation loop.

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

        # Confirm all
        if choice == "":
            return list(attributes), False

        # Skip question entirely
        if choice.lower() == "s":
            return None, False

        # Rephrase and retry
        if choice.lower() == "r":
            return [], True

        # Edit a specific attribute value
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
            continue  # re-display preview

        # Skip specific numbered attributes
        try:
            skip_set = {int(x.strip()) for x in choice.split(",") if x.strip()}
            confirmed = [a for i, a in enumerate(attributes, 1) if i not in skip_set]
            return confirmed, False
        except ValueError:
            print("  Unrecognised input. Press Enter to confirm all, or type numbers like '2,3' to skip.")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_domain_id(conn, domain_name: str) -> str:
    row = conn.execute(
        "SELECT id FROM domains WHERE name = ?", (domain_name,)
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"Domain '{domain_name}' not found. Run 'make init' to seed domains."
        )
    return row[0]


def find_existing_active(conn, domain_id: str, label: str):
    """Return (id, value, confidence) for the active attribute, or None."""
    return conn.execute(
        "SELECT id, value, confidence FROM attributes "
        "WHERE domain_id = ? AND label = ? AND status = 'active'",
        (domain_id, label),
    ).fetchone()


def _insert_attribute_row(conn, domain_id: str, attr: dict, now: str) -> None:
    conn.execute(
        "INSERT INTO attributes "
        "(id, domain_id, label, value, elaboration, mutability, source, "
        "confidence, routing, status, created_at, updated_at, last_confirmed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (
            str(uuid.uuid4()),
            domain_id,
            attr["label"],
            attr["value"],
            attr.get("elaboration"),
            attr.get("mutability", STABLE),
            REFLECTION,
            float(attr.get("confidence", 0.8)),
            LOCAL_ONLY,
            now,
            now,
            now,
        ),
    )


def write_attribute(conn, domain_id: str, attr: dict, old_row) -> str:
    """
    Write a single attribute.
    If old_row is not None, supersede it and record history first.
    Returns 'updated' or 'created'.
    """
    now = datetime.datetime.now(datetime.UTC).isoformat()

    if old_row is not None:
        old_id, old_value, old_confidence = old_row
        conn.execute(
            "UPDATE attributes SET status = 'superseded', updated_at = ? WHERE id = ?",
            (now, old_id),
        )
        conn.execute(
            "INSERT INTO attribute_history "
            "(id, attribute_id, previous_value, previous_confidence, reason, changed_at, changed_by) "
            "VALUES (?, ?, ?, ?, ?, ?, 'reflection')",
            (
                str(uuid.uuid4()),
                old_id,
                old_value,
                old_confidence,
                "superseded by interview session",
                now,
            ),
        )
        _insert_attribute_row(conn, domain_id, attr, now)
        conn.commit()
        return "updated"

    _insert_attribute_row(conn, domain_id, attr, now)
    conn.commit()
    return "created"


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

def interview_question(question: str, domain_name: str) -> tuple:
    """
    Run the full ask → extract → preview → confirm → write cycle for one question.

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

        # Extract attributes from Ollama
        print()
        try:
            raw = _run_with_elapsed(
                "Extracting attributes...",
                lambda: call_ollama(question, answer),
            )
        except requests.exceptions.Timeout:
            print(f"Timed out after {OLLAMA_TIMEOUT}s.")
            print("Would you like to retry? (y/n)")
            if input("> ").strip().lower() == "y":
                continue
            return created, updated
        except Exception as exc:
            print(f"Error: {exc}")
            print("Would you like to retry? (y/n)")
            if input("> ").strip().lower() == "y":
                continue
            return created, updated

        try:
            attributes = parse_attributes(raw)
        except (json.JSONDecodeError, ValueError):
            print("Response was not valid JSON.")
            print(f"Raw response:\n{raw[:600]}")
            print("\nWould you like to rephrase your answer and retry? (y/n)")
            if input("> ").strip().lower() == "y":
                continue
            return created, updated

        if not attributes:
            print("No attributes extracted.")
            return created, updated

        # Confirmation loop
        confirmed, retry = confirm_attributes(attributes)

        if retry:
            continue  # user wants to rephrase

        if confirmed is None:
            print("  (Question skipped.)")
            return created, updated

        if not confirmed:
            print("  (All attributes skipped.)")
            return created, updated

        # Write each confirmed attribute immediately
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

                outcome = write_attribute(conn, domain_id, attr, existing if existing and upd == "y" else None)
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

def run_interview() -> None:
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
                q_created, q_updated = interview_question(question, domain_name)
                domain_created += q_created
                domain_updated += q_updated

            total_created += domain_created
            total_updated += domain_updated
            if domain_name not in domains_covered:
                domains_covered.append(domain_name)

            n_saved = domain_created + domain_updated
            print(f"\nDomain complete: {n_saved} attribute(s) saved.")

            # Prompt to continue — skip after the last domain
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

    # Session summary
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
        print("Session saved. Run 'make test' to verify your data.")
    except Exception as exc:
        print(f"Warning: Could not save session record: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("identity-engine — Identity Interview")
    print("━" * 40)

    ollama_proc = None
    try:
        ollama_proc = ensure_ollama()
        ensure_model()

        print("Checking database...", end=" ", flush=True)
        check_database()
        print("OK")

        print()
        print("This session will guide you through questions across your selected identity domains.")
        print("Answers are processed locally by Ollama (llama3.1:8b) — no external API calls.")
        print("Nothing is written to the database without your explicit confirmation.")

        run_interview()
    finally:
        if ollama_proc is not None:
            ollama_proc.terminate()
            try:
                ollama_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ollama_proc.kill()


if __name__ == "__main__":
    main()
