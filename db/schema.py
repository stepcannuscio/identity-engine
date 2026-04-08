"""Database schema definitions and table creation."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS domains (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attributes (
    id             TEXT PRIMARY KEY,
    domain_id      TEXT NOT NULL REFERENCES domains(id) ON DELETE RESTRICT,
    label          TEXT NOT NULL,
    value          TEXT NOT NULL,
    elaboration    TEXT,
    mutability     TEXT NOT NULL CHECK(mutability IN ('stable', 'evolving')),
    source         TEXT NOT NULL CHECK(source IN ('explicit', 'inferred', 'reflection')),
    confidence     REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    routing        TEXT NOT NULL DEFAULT 'local_only'
                       CHECK(routing IN ('local_only', 'external_ok')),
    status         TEXT NOT NULL DEFAULT 'active'
                       CHECK(status IN ('active', 'superseded', 'retracted')),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_confirmed TIMESTAMP
);

-- Only one active attribute per label per domain at a time
CREATE UNIQUE INDEX IF NOT EXISTS uq_attributes_active_label
    ON attributes(domain_id, label)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS attribute_history (
    id                  TEXT PRIMARY KEY,
    attribute_id        TEXT NOT NULL REFERENCES attributes(id) ON DELETE RESTRICT,
    previous_value      TEXT NOT NULL,
    previous_confidence REAL,
    reason              TEXT,
    changed_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    changed_by          TEXT NOT NULL CHECK(changed_by IN ('user', 'reflection', 'inferred'))
);

CREATE TABLE IF NOT EXISTS inference_evidence (
    id             TEXT PRIMARY KEY,
    attribute_id   TEXT NOT NULL REFERENCES attributes(id) ON DELETE RESTRICT,
    source_type    TEXT NOT NULL,
    source_ref     TEXT,
    supporting_text TEXT,
    weight         REAL CHECK(weight BETWEEN 0.0 AND 1.0),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reflection_sessions (
    id                  TEXT PRIMARY KEY,
    session_type        TEXT NOT NULL CHECK(
                            session_type IN ('guided', 'freeform', 'vault_analysis')
                        ),
    summary             TEXT,
    attributes_created  INTEGER DEFAULT 0,
    attributes_updated  INTEGER DEFAULT 0,
    external_calls_made INTEGER DEFAULT 0,
    routing_log         TEXT,
    started_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at            TIMESTAMP
);
"""

INITIAL_DOMAINS = [
    ("personality", "Core personality traits and characteristics"),
    ("values",      "Deeply held values and ethical commitments"),
    ("goals",       "Short-term and long-term goals and aspirations"),
    ("patterns",    "Recurring behavioural patterns and habits"),
    ("voice",       "Communication style, tone, and expression"),
    ("relationships", "Attitudes and patterns around relationships"),
    ("fears",       "Fears, anxieties, and avoidance patterns"),
    ("beliefs",     "Beliefs about the world, self, and others"),
]


def create_tables(conn) -> None:
    """Execute the full schema DDL against the given connection."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def seed_domains(conn) -> list[str]:
    """Insert the initial domains if they do not already exist.

    Returns the list of domain names that were actually inserted.
    """
    import uuid

    created = []
    for name, description in INITIAL_DOMAINS:
        existing = conn.execute(
            "SELECT id FROM domains WHERE name = ?", (name,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO domains (id, name, description) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), name, description),
            )
            created.append(name)
    conn.commit()
    return created
