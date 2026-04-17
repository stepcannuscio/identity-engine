#!/usr/bin/env python3
"""
view_db.py — Pretty-print the identity store grouped by domain.

Run via:  make view
          .venv/bin/python scripts/view_db.py
"""

import sys
import os
from collections import OrderedDict

# Project root must be on the path so db/ and config/ are importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.connection import get_connection


def _bar(char="─", width=48):
    return char * width


def _domain_header(name, count):
    label = f"── {name.upper()} ({count}) "
    return label + "─" * max(0, 48 - len(label))


def view(conn):
    """Pretty-print the identity store to stdout using an open connection."""
    rows = conn.execute("""
        SELECT d.name,
               a.label, a.value, a.elaboration,
               a.mutability, a.source, a.confidence, a.routing,
               a.updated_at
        FROM domains d
        LEFT JOIN attributes a
               ON a.domain_id = d.id AND a.status IN ('active', 'confirmed')
        ORDER BY d.name, a.label
    """).fetchall()

    # Group rows by domain
    domains = OrderedDict()
    last_updated = None

    for (domain, label, value, elaboration,
         mutability, source, confidence, routing, updated_at) in rows:
        if domain not in domains:
            domains[domain] = []
        if label is not None:
            domains[domain].append({
                "label": label,
                "value": value,
                "elaboration": elaboration,
                "mutability": mutability,
                "source": source,
                "confidence": confidence,
                "routing": routing,
                "updated_at": updated_at,
            })
            if updated_at and (last_updated is None or updated_at > last_updated):
                last_updated = updated_at

    total_attrs = sum(len(attrs) for attrs in domains.values())
    domains_with_data = sum(1 for attrs in domains.values() if attrs)
    domains_empty = len(domains) - domains_with_data

    # Header
    print()
    print(_bar("═"))
    summary_line = "  IDENTITY STORE"
    if total_attrs:
        summary_line += (
            f"  —  {total_attrs} attribute{'s' if total_attrs != 1 else ''}"
            f" across {domains_with_data} domain{'s' if domains_with_data != 1 else ''}"
        )
    else:
        summary_line += "  —  no attributes stored yet"
    print(summary_line)
    print(_bar("═"))

    for domain_name, attrs in domains.items():
        print()
        print(_domain_header(domain_name, len(attrs)))

        if not attrs:
            print("  (no current attributes)")
            continue

        # Align labels to the longest one in this domain
        max_label = max(len(a["label"]) for a in attrs)

        for a in attrs:
            conf = f"{a['confidence']:.2f}" if a["confidence"] is not None else "?"
            badge = f"[{a['mutability']}, {a['source']}, {conf}]"
            routing_tag = a["routing"] or ""
            label_padded = a["label"].ljust(max_label)
            print(f"  {label_padded}  {badge} {routing_tag}")
            if a["value"]:
                print(f"    {a['value']}")
            if a["elaboration"]:
                print(f"    {a['elaboration']}")

    # Footer
    print()
    print(_bar())
    parts = []
    if domains_with_data:
        parts.append(f"{domains_with_data} domain{'s' if domains_with_data != 1 else ''} with data")
    if domains_empty:
        parts.append(f"{domains_empty} empty")
    parts.append(f"{total_attrs} total attribute{'s' if total_attrs != 1 else ''}")
    print("  " + "  ·  ".join(parts))
    if last_updated:
        print(f"  Last updated: {last_updated[:19]}")
    print(_bar())
    print()


def main():
    try:
        with get_connection() as conn:
            view(conn)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Run 'make init' to initialise the database.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
