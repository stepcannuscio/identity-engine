"""Canonical settings, constants, and keychain access for identity-engine."""

import logging

from pathlib import Path

import keyring
from keyring.errors import KeyringError

logger = logging.getLogger(__name__)

DB_DIR = Path.home() / ".identity-engine"
DB_PATH = DB_DIR / "identity.db"

_KEYRING_SERVICE = "identity-engine"
_KEYRING_USERNAME = "db-encryption-key"
_UI_PASSPHRASE_USERNAME = "ui-passphrase"

SQLCIPHER_PBKDF2_ITER = 256000
SQLCIPHER_PAGE_SIZE = 4096

# Routing constants
LOCAL_ONLY = "local_only"
EXTERNAL_OK = "external_ok"

# Source constants
EXPLICIT = "explicit"
INFERRED = "inferred"
REFLECTION = "reflection"

# Mutability constants
STABLE = "stable"
EVOLVING = "evolving"


_PROVIDER_KEY_MAP = {
    "anthropic": "anthropic-api-key",
    "groq":      "groq-api-key",
}


def get_api_key(provider: str) -> str | None:
    """Retrieve an API key from the system keychain by provider name.

    Supported providers: "anthropic", "groq".
    Returns None if no key is stored for that provider.
    Never logs, prints, or raises with the key value included.
    """
    keyring_username = _PROVIDER_KEY_MAP.get(provider)
    if keyring_username is None:
        return None
    try:
        return keyring.get_password(_KEYRING_SERVICE, keyring_username) or None
    except KeyringError as exc:
        logger.warning("Unable to read %s API key from keychain: %s", provider, exc)
        return None


def has_api_key(provider: str) -> bool:
    """Return True when a provider key exists in the system keychain."""
    return bool(get_api_key(provider))


def set_api_key(provider: str, api_key: str) -> None:
    """Store an API key for a supported provider in the system keychain."""
    keyring_username = _PROVIDER_KEY_MAP.get(provider)
    if keyring_username is None:
        raise ValueError(f"Unsupported provider: {provider}")
    keyring.set_password(_KEYRING_SERVICE, keyring_username, api_key)


def delete_api_key(provider: str) -> None:
    """Delete an API key for a supported provider from the system keychain."""
    keyring_username = _PROVIDER_KEY_MAP.get(provider)
    if keyring_username is None:
        raise ValueError(f"Unsupported provider: {provider}")
    try:
        keyring.delete_password(_KEYRING_SERVICE, keyring_username)
    except KeyringError:
        return


def get_db_key() -> str:
    """Retrieve the database encryption key from the system keychain.

    Raises RuntimeError if the key has not been stored yet.
    Never returns None or an empty string.
    """
    try:
        key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except KeyringError as exc:
        raise RuntimeError(
            "Database encryption key could not be read from the system keychain. "
            "Unlock the keychain or re-run 'make init' in an interactive session."
        ) from exc
    if not key:
        raise RuntimeError(
            "Database encryption key not found in system keychain. "
            f"Service: '{_KEYRING_SERVICE}', Username: '{_KEYRING_USERNAME}'. "
            "Run 'make init' or 'python scripts/init_db.py' to generate and store the key."
        )
    return key


def get_ui_passphrase() -> str | None:
    """Return the UI passphrase from the system keychain, if configured."""
    try:
        return keyring.get_password(_KEYRING_SERVICE, _UI_PASSPHRASE_USERNAME) or None
    except KeyringError as exc:
        logger.warning("Unable to read UI passphrase from keychain: %s", exc)
        return None


def set_ui_passphrase(passphrase: str) -> None:
    """Store the UI passphrase in the system keychain."""
    keyring.set_password(_KEYRING_SERVICE, _UI_PASSPHRASE_USERNAME, passphrase)
