from contextlib import contextmanager
from pathlib import Path
from config.settings import DB_PATH, SQLCIPHER_PAGE_SIZE, SQLCIPHER_PBKDF2_ITER, get_db_key


def _apply_pragmas(conn, key: str) -> None:
    """Apply SQLCipher and SQLite pragmas. Key is used only here and never stored."""
    cursor = conn.cursor()
    # SQLCipher key must be set before any other operation
    cursor.execute(f"PRAGMA key = \"{key}\";")
    cursor.execute(f"PRAGMA cipher_page_size = {SQLCIPHER_PAGE_SIZE};")
    cursor.execute(f"PRAGMA kdf_iter = {SQLCIPHER_PBKDF2_ITER};")
    cursor.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512;")
    cursor.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
    # SQLite pragmas
    cursor.execute(f"PRAGMA page_size = {SQLCIPHER_PAGE_SIZE};")
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.close()


@contextmanager
def get_connection(db_path: Path = DB_PATH):
    """Context manager that yields an open, authenticated SQLCipher connection.

    Usage:
        with get_connection() as conn:
            conn.execute(...)

    The key is retrieved from the system keychain and never accepted as a parameter.
    """
    key = get_db_key()

    conn = None
    try:
        try:
            from sqlcipher3 import dbapi2 as sqlcipher
        except ImportError:
            raise RuntimeError(
                        "sqlcipher3 is not installed. "
                        "Run 'pip install sqlcipher3'."
                    ) from exc
                    
        conn = sqlcipher.connect(str(db_path))
        _apply_pragmas(conn, key)

        # Verify the key is correct by running a trivial query
        try:
            conn.execute("SELECT count(*) FROM sqlite_master;")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to open encrypted database at {db_path}. "
                "The key may be incorrect or the file may be corrupt."
            ) from exc

        yield conn

    finally:
        del key  # ensure key is not held in scope after connection closes
        if conn is not None:
            conn.close()


@contextmanager
def get_plain_connection(db_path: str = ":memory:"):
    """Plain (unencrypted) SQLite connection for testing without SQLCipher.

    Uses the standard sqlite3 module. Only for use in tests.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()
