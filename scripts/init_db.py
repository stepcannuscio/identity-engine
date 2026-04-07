#!/usr/bin/env python3
"""Initialise the identity-engine database.

Safe to run multiple times (idempotent).
"""

import os
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import base64
import secrets

import keyring

from config.settings import DB_DIR, DB_PATH, _KEYRING_SERVICE, _KEYRING_USERNAME
from db.connection import get_connection
from db.schema import create_tables, seed_domains


def ensure_db_directory() -> None:
    """Create ~/.identity-engine/ with permissions 700 if it does not exist."""
    if not DB_DIR.exists():
        DB_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(DB_DIR, 0o700)
        print(f"Created directory: {DB_DIR} (permissions: 700)")
    else:
        print(f"Directory already exists: {DB_DIR}")
        # Enforce permissions even if the directory pre-existed
        os.chmod(DB_DIR, 0o700)


def ensure_keychain_key() -> bool:
    """Store a new encryption key in the keychain if one does not already exist.

    Returns True if a new key was generated, False if one already existed.
    Never overwrites an existing key.
    """
    existing = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    if existing:
        print("Encryption key already exists in keychain — not overwriting.")
        return False

    raw_key = secrets.token_bytes(32)
    encoded_key = base64.b64encode(raw_key).decode("ascii")
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, encoded_key)
    print("Generated new 32-byte encryption key and stored it in the system keychain.")
    print(f"  Service:  {_KEYRING_SERVICE}")
    print(f"  Username: {_KEYRING_USERNAME}")
    return True


def main() -> None:
    print("=== identity-engine initialisation ===\n")

    ensure_db_directory()
    key_was_new = ensure_keychain_key()

    print(f"\nOpening database: {DB_PATH}")
    with get_connection(DB_PATH) as conn:
        create_tables(conn)
        print("Schema created (or already exists).")

        created_domains = seed_domains(conn)
        if created_domains:
            print(f"Seeded {len(created_domains)} domain(s): {', '.join(created_domains)}")
        else:
            print("All domains already present — nothing to seed.")

    print("\n=== Initialisation complete ===")
    if key_was_new:
        print("IMPORTANT: Your encryption key is stored in the system keychain.")
        print("If you lose it, the database cannot be recovered. Back up your keychain.")


if __name__ == "__main__":
    main()
