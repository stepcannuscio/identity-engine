from pathlib import Path
import keyring

DB_DIR = Path.home() / ".identity-engine"
DB_PATH = DB_DIR / "identity.db"

_KEYRING_SERVICE = "identity-engine"
_KEYRING_USERNAME = "db-encryption-key"

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


def get_db_key() -> str:
    """Retrieve the database encryption key from the system keychain.

    Raises RuntimeError if the key has not been stored yet.
    Never returns None or an empty string.
    """
    key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    if not key:
        raise RuntimeError(
            "Database encryption key not found in system keychain. "
            f"Service: '{_KEYRING_SERVICE}', Username: '{_KEYRING_USERNAME}'. "
            "Run 'make init' or 'python scripts/init_db.py' to generate and store the key."
        )
    return key
