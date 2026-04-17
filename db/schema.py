"""Database schema definitions and table creation."""

DOMAINS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS domains (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

ATTRIBUTES_TABLE_SQL = """
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
                       CHECK(status IN (
                           'active',
                           'confirmed',
                           'superseded',
                           'rejected',
                           'retracted'
                       )),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_confirmed TIMESTAMP
);
"""

ATTRIBUTES_CURRENT_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_attributes_active_label
    ON attributes(domain_id, label)
    WHERE status IN ('active', 'confirmed');
"""

ATTRIBUTE_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS attribute_history (
    id                  TEXT PRIMARY KEY,
    attribute_id        TEXT NOT NULL REFERENCES attributes(id) ON DELETE RESTRICT,
    previous_value      TEXT NOT NULL,
    previous_confidence REAL,
    reason              TEXT,
    changed_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    changed_by          TEXT NOT NULL CHECK(changed_by IN ('user', 'reflection', 'inferred'))
);
"""

INFERENCE_EVIDENCE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS inference_evidence (
    id             TEXT PRIMARY KEY,
    attribute_id   TEXT NOT NULL REFERENCES attributes(id) ON DELETE RESTRICT,
    source_type    TEXT NOT NULL,
    source_ref     TEXT,
    supporting_text TEXT,
    weight         REAL CHECK(weight BETWEEN 0.0 AND 1.0),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

ARTIFACTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS artifacts (
    id          TEXT PRIMARY KEY,
    domain_id   TEXT REFERENCES domains(id) ON DELETE SET NULL,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    source      TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

ARTIFACT_CHUNKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS artifact_chunks (
    id          TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    metadata    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

ARTIFACT_CHUNKS_ORDER_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_chunks_position
    ON artifact_chunks(artifact_id, chunk_index);
"""

ARTIFACT_TAGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS artifact_tags (
    id          TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

ARTIFACT_TAGS_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_tag_per_artifact
    ON artifact_tags(artifact_id, tag);
"""

REFLECTION_SESSIONS_TABLE_SQL = """
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

PREFERENCE_SIGNALS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS preference_signals (
    id           TEXT PRIMARY KEY,
    category     TEXT NOT NULL,
    subject      TEXT NOT NULL,
    signal       TEXT NOT NULL CHECK(signal IN (
                     'like', 'dislike', 'accept', 'reject', 'prefer', 'avoid'
                 )),
    strength     INTEGER NOT NULL DEFAULT 3 CHECK(strength BETWEEN 1 AND 5),
    source       TEXT NOT NULL CHECK(source IN (
                     'explicit_feedback', 'behavior', 'correction', 'system_inference'
                 )),
    context_json TEXT,
    attribute_id TEXT REFERENCES attributes(id) ON DELETE SET NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

PREFERENCE_SIGNALS_LOOKUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_preference_signals_lookup
    ON preference_signals(category, subject, created_at DESC);
"""

APP_SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_settings (
    id                    INTEGER PRIMARY KEY CHECK(id = 1),
    onboarding_completed  INTEGER NOT NULL DEFAULT 0 CHECK(onboarding_completed IN (0, 1)),
    active_profile        TEXT,
    preferred_backend     TEXT NOT NULL DEFAULT 'local'
                             CHECK(preferred_backend IN ('local', 'external')),
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

PROVIDER_STATUS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS provider_status (
    provider          TEXT PRIMARY KEY CHECK(provider IN ('ollama', 'anthropic', 'groq')),
    configured        INTEGER NOT NULL DEFAULT 0 CHECK(configured IN (0, 1)),
    validated         INTEGER NOT NULL DEFAULT 0 CHECK(validated IN (0, 1)),
    last_validated_at TIMESTAMP,
    last_error        TEXT,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

TEACH_QUESTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS teach_questions (
    id               TEXT PRIMARY KEY,
    prompt           TEXT NOT NULL,
    domain           TEXT REFERENCES domains(name) ON DELETE SET NULL,
    intent_key       TEXT NOT NULL,
    source           TEXT NOT NULL CHECK(source IN ('catalog', 'generated')),
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK(status IN ('pending', 'answered', 'dismissed')),
    priority         REAL NOT NULL DEFAULT 0.0,
    onboarding_stage TEXT NOT NULL DEFAULT 'teaching'
                         CHECK(onboarding_stage IN ('welcome', 'privacy', 'security', 'teaching')),
    asked_count      INTEGER NOT NULL DEFAULT 0,
    answer_count     INTEGER NOT NULL DEFAULT 0,
    last_presented_at TIMESTAMP,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

TEACH_QUESTIONS_LOOKUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_teach_questions_status_priority
    ON teach_questions(status, onboarding_stage, priority DESC, updated_at DESC);
"""

TEACH_FEEDBACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS teach_question_feedback (
    id          TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES teach_questions(id) ON DELETE CASCADE,
    feedback    TEXT NOT NULL CHECK(feedback IN (
                    'skip',
                    'not_relevant',
                    'duplicate',
                    'already_covered',
                    'too_personal'
                 )),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

TEACH_FEEDBACK_LOOKUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_teach_feedback_question_created
    ON teach_question_feedback(question_id, created_at DESC);
"""

SCHEMA_SQL = "\n\n".join(
    [
        DOMAINS_TABLE_SQL,
        ATTRIBUTES_TABLE_SQL,
        ATTRIBUTES_CURRENT_INDEX_SQL,
        ATTRIBUTE_HISTORY_TABLE_SQL,
        INFERENCE_EVIDENCE_TABLE_SQL,
        ARTIFACTS_TABLE_SQL,
        ARTIFACT_CHUNKS_TABLE_SQL,
        ARTIFACT_CHUNKS_ORDER_INDEX_SQL,
        ARTIFACT_TAGS_TABLE_SQL,
        ARTIFACT_TAGS_UNIQUE_INDEX_SQL,
        REFLECTION_SESSIONS_TABLE_SQL,
        PREFERENCE_SIGNALS_TABLE_SQL,
        PREFERENCE_SIGNALS_LOOKUP_INDEX_SQL,
        APP_SETTINGS_TABLE_SQL,
        PROVIDER_STATUS_TABLE_SQL,
        TEACH_QUESTIONS_TABLE_SQL,
        TEACH_QUESTIONS_LOOKUP_INDEX_SQL,
        TEACH_FEEDBACK_TABLE_SQL,
        TEACH_FEEDBACK_LOOKUP_INDEX_SQL,
    ]
)

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


def _read_schema_sql(conn, *, kind: str, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
        (kind, name),
    ).fetchone()
    if row is None or row[0] is None:
        return ""
    return str(row[0]).lower()


def _attributes_schema_needs_migration(conn) -> bool:
    attributes_sql = _read_schema_sql(conn, kind="table", name="attributes")
    if not attributes_sql:
        return False

    index_sql = _read_schema_sql(conn, kind="index", name="uq_attributes_active_label")
    return (
        "confirmed" not in attributes_sql
        or "rejected" not in attributes_sql
        or "confirmed" not in index_sql
    )


def _migrate_attribute_tables(conn) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("DROP INDEX IF EXISTS uq_attributes_active_label")
        conn.execute("ALTER TABLE attributes RENAME TO attributes_old")
        conn.execute("ALTER TABLE attribute_history RENAME TO attribute_history_old")
        conn.execute("ALTER TABLE inference_evidence RENAME TO inference_evidence_old")
        conn.executescript(
            "\n\n".join(
                [
                    ATTRIBUTES_TABLE_SQL,
                    ATTRIBUTES_CURRENT_INDEX_SQL,
                    ATTRIBUTE_HISTORY_TABLE_SQL,
                    INFERENCE_EVIDENCE_TABLE_SQL,
                ]
            )
        )
        conn.execute(
            """
            INSERT INTO attributes (
                id, domain_id, label, value, elaboration, mutability, source, confidence,
                routing, status, created_at, updated_at, last_confirmed
            )
            SELECT
                id, domain_id, label, value, elaboration, mutability, source, confidence,
                routing, status, created_at, updated_at, last_confirmed
            FROM attributes_old
            """
        )
        conn.execute(
            """
            INSERT INTO attribute_history (
                id, attribute_id, previous_value,
                previous_confidence, reason, changed_at, changed_by
            )
            SELECT
                id, attribute_id, previous_value,
                previous_confidence, reason, changed_at, changed_by
            FROM attribute_history_old
            """
        )
        conn.execute(
            """
            INSERT INTO inference_evidence (
                id, attribute_id, source_type, source_ref, supporting_text, weight, created_at
            )
            SELECT
                id, attribute_id, source_type, source_ref, supporting_text, weight, created_at
            FROM inference_evidence_old
            """
        )
        conn.execute("DROP TABLE inference_evidence_old")
        conn.execute("DROP TABLE attribute_history_old")
        conn.execute("DROP TABLE attributes_old")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def create_tables(conn) -> None:
    """Execute the full schema DDL against the given connection."""
    conn.executescript(SCHEMA_SQL)
    if _attributes_schema_needs_migration(conn):
        _migrate_attribute_tables(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO app_settings (id, onboarding_completed, active_profile, preferred_backend)
        VALUES (1, 0, NULL, 'local')
        """
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO provider_status (provider, configured, validated, last_error)
        VALUES (?, 0, 0, NULL)
        """,
        [("ollama",), ("anthropic",), ("groq",)],
    )
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
