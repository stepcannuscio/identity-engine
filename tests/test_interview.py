"""Tests for scripts/seed_interview.py.

Covers DB helpers, JSON parsing, confirmation UI logic, Ollama call
structure, domain constant correctness, and the full per-question flow.

All database operations use get_plain_connection() (unencrypted, in-memory)
so SQLCipher is not required in CI.
"""

import json
import sys
import uuid
import datetime
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_plain_connection
from db.schema import create_tables, seed_domains
import scripts.seed_interview as interview
import config.llm_router as llm_router
from config.llm_router import (
    _parse_json_response as parse_attributes,
    _call_ollama,
    _ollama_is_running,
    _start_ollama,
    _ensure_local_model,
    ProviderConfig
)

# Shim: the old call_ollama(question, answer) is now split across
# _build_messages + _call_ollama; tests below use the new interface.

OLLAMA_TIMEOUT = llm_router.OLLAMA_TIMEOUT


def _local_config(model="llama3.1:8b") -> ProviderConfig:
    return ProviderConfig(
        provider="ollama", api_key=None, model=model,
        is_local=True, arch="apple_silicon", ram_gb=36.0,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory SQLite connection with schema and all eight domains seeded."""
    with get_plain_connection(":memory:") as c:
        create_tables(c)
        seed_domains(c)
        yield c


@pytest.fixture
def domain_id(conn):
    """UUID of the 'personality' domain."""
    return conn.execute(
        "SELECT id FROM domains WHERE name = 'personality'"
    ).fetchone()[0]


@pytest.fixture
def mock_get_connection(conn):
    """Returns a patchable context-manager factory that yields conn."""
    @contextmanager
    def _factory():
        yield conn
    return _factory


SAMPLE_ATTR = {
    "label": "recharge_style",
    "value": "I recharge through solitude and quiet time.",
    "elaboration": "Most pronounced after large social events.",
    "mutability": "stable",
    "confidence": 0.85,
}

OLLAMA_JSON = json.dumps([SAMPLE_ATTR])

VALID_OLLAMA_RESPONSE = {
    "message": {"content": OLLAMA_JSON}
}


# ---------------------------------------------------------------------------
# parse_attributes
# ---------------------------------------------------------------------------

def test_parse_attributes_valid_json():
    raw = json.dumps([SAMPLE_ATTR])
    result = parse_attributes(raw)
    assert len(result) == 1
    assert result[0]["label"] == "recharge_style"


def test_parse_attributes_strips_backtick_fences():
    raw = f"```json\n{json.dumps([SAMPLE_ATTR])}\n```"
    result = parse_attributes(raw)
    assert result[0]["label"] == "recharge_style"


def test_parse_attributes_strips_plain_fences():
    raw = f"```\n{json.dumps([SAMPLE_ATTR])}\n```"
    result = parse_attributes(raw)
    assert result[0]["label"] == "recharge_style"


def test_parse_attributes_multiple_attrs():
    attrs = [SAMPLE_ATTR, {**SAMPLE_ATTR, "label": "decision_style"}]
    result = parse_attributes(json.dumps(attrs))
    assert len(result) == 2
    assert result[1]["label"] == "decision_style"


def test_parse_attributes_raises_on_invalid_json():
    with pytest.raises((json.JSONDecodeError, ValueError)):
        parse_attributes("not json at all")


def test_parse_attributes_raises_on_truncated_json():
    with pytest.raises((json.JSONDecodeError, ValueError)):
        parse_attributes('[{"label": "x"')


# ---------------------------------------------------------------------------
# get_domain_id
# ---------------------------------------------------------------------------

def test_get_domain_id_returns_uuid(conn):
    did = interview.get_domain_id(conn, "personality")
    # UUID v4 is 36 chars including hyphens
    assert len(did) == 36
    assert did.count("-") == 4


def test_get_domain_id_all_eight_domains(conn):
    for name in ("personality", "values", "goals", "patterns",
                 "voice", "relationships", "fears", "beliefs"):
        did = interview.get_domain_id(conn, name)
        assert did  # not empty


def test_get_domain_id_raises_for_unknown_domain(conn):
    with pytest.raises(RuntimeError, match="not found"):
        interview.get_domain_id(conn, "nonexistent-domain")


# ---------------------------------------------------------------------------
# find_existing_active
# ---------------------------------------------------------------------------

def _insert_raw_attribute(conn, domain_id, label, status="active", value="v"):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    aid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO attributes "
        "(id, domain_id, label, value, mutability, source, confidence, routing, status, "
        "created_at, updated_at, last_confirmed) "
        "VALUES (?, ?, ?, ?, 'stable', 'reflection', 0.8, 'local_only', ?, ?, ?, ?)",
        (aid, domain_id, label, value, status, now, now, now),
    )
    conn.commit()
    return aid


def test_find_existing_active_none_when_absent(conn, domain_id):
    assert interview.find_existing_active(conn, domain_id, "missing_label") is None


def test_find_existing_active_returns_row(conn, domain_id):
    aid = _insert_raw_attribute(conn, domain_id, "test_label", value="hello")
    row = interview.find_existing_active(conn, domain_id, "test_label")
    assert row is not None
    assert row[0] == aid   # id
    assert row[1] == "hello"  # value
    assert row[2] == pytest.approx(0.8)  # confidence


def test_find_existing_active_ignores_superseded(conn, domain_id):
    _insert_raw_attribute(conn, domain_id, "old_label", status="superseded")
    assert interview.find_existing_active(conn, domain_id, "old_label") is None


def test_find_existing_active_ignores_retracted(conn, domain_id):
    _insert_raw_attribute(conn, domain_id, "retracted_label", status="retracted")
    assert interview.find_existing_active(conn, domain_id, "retracted_label") is None


# ---------------------------------------------------------------------------
# write_attribute — new creation
# ---------------------------------------------------------------------------

def test_write_attribute_creates_new_returns_created(conn, domain_id):
    outcome = interview.write_attribute(conn, domain_id, SAMPLE_ATTR, None)
    assert outcome == "created"


def test_write_attribute_new_row_in_db(conn, domain_id):
    interview.write_attribute(conn, domain_id, SAMPLE_ATTR, None)
    row = conn.execute(
        "SELECT label, value, elaboration, mutability, confidence, status "
        "FROM attributes WHERE label = 'recharge_style'"
    ).fetchone()
    assert row is not None
    assert row[0] == "recharge_style"
    assert row[1] == SAMPLE_ATTR["value"]
    assert row[3] == "stable"
    assert row[4] == pytest.approx(0.85)
    assert row[5] == "active"


def test_write_attribute_source_is_always_reflection(conn, domain_id):
    interview.write_attribute(conn, domain_id, SAMPLE_ATTR, None)
    src = conn.execute(
        "SELECT source FROM attributes WHERE label = 'recharge_style'"
    ).fetchone()[0]
    assert src == "reflection"


def test_write_attribute_routing_is_always_local_only(conn, domain_id):
    interview.write_attribute(conn, domain_id, SAMPLE_ATTR, None)
    routing = conn.execute(
        "SELECT routing FROM attributes WHERE label = 'recharge_style'"
    ).fetchone()[0]
    assert routing == "local_only"


def test_write_attribute_null_elaboration_stored(conn, domain_id):
    attr = {**SAMPLE_ATTR, "elaboration": None}
    interview.write_attribute(conn, domain_id, attr, None)
    elab = conn.execute(
        "SELECT elaboration FROM attributes WHERE label = 'recharge_style'"
    ).fetchone()[0]
    assert elab is None


# ---------------------------------------------------------------------------
# write_attribute — supersede path
# ---------------------------------------------------------------------------

def test_write_attribute_supersede_returns_updated(conn, domain_id):
    old_id = _insert_raw_attribute(conn, domain_id, "recharge_style", value="old")
    old_row = (old_id, "old", 0.7)
    outcome = interview.write_attribute(conn, domain_id, SAMPLE_ATTR, old_row)
    assert outcome == "updated"


def test_write_attribute_supersede_marks_old_as_superseded(conn, domain_id):
    old_id = _insert_raw_attribute(conn, domain_id, "recharge_style", value="old")
    old_row = (old_id, "old", 0.7)
    interview.write_attribute(conn, domain_id, SAMPLE_ATTR, old_row)
    old_status = conn.execute(
        "SELECT status FROM attributes WHERE id = ?", (old_id,)
    ).fetchone()[0]
    assert old_status == "superseded"


def test_write_attribute_supersede_creates_new_active(conn, domain_id):
    old_id = _insert_raw_attribute(conn, domain_id, "recharge_style", value="old")
    old_row = (old_id, "old", 0.7)
    interview.write_attribute(conn, domain_id, SAMPLE_ATTR, old_row)
    rows = conn.execute(
        "SELECT value, status FROM attributes WHERE label = 'recharge_style' AND status = 'active'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == SAMPLE_ATTR["value"]


def test_write_attribute_supersede_writes_history(conn, domain_id):
    old_id = _insert_raw_attribute(conn, domain_id, "recharge_style", value="old value")
    old_row = (old_id, "old value", 0.7)
    interview.write_attribute(conn, domain_id, SAMPLE_ATTR, old_row)
    hist = conn.execute(
        "SELECT attribute_id, previous_value, changed_by FROM attribute_history"
    ).fetchone()
    assert hist is not None
    assert hist[0] == old_id
    assert hist[1] == "old value"
    assert hist[2] == "reflection"


def test_write_attribute_no_history_on_new_create(conn, domain_id):
    interview.write_attribute(conn, domain_id, SAMPLE_ATTR, None)
    count = conn.execute("SELECT count(*) FROM attribute_history").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# write_reflection_session
# ---------------------------------------------------------------------------

def test_write_reflection_session_inserts_record(conn):
    started = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    interview.write_reflection_session(conn, started, ["personality", "values"], 4, 1)
    row = conn.execute(
        "SELECT session_type, attributes_created, attributes_updated, external_calls_made "
        "FROM reflection_sessions"
    ).fetchone()
    assert row[0] == "guided"
    assert row[1] == 4
    assert row[2] == 1
    assert row[3] == 0  # Ollama is local — always zero


def test_write_reflection_session_summary_contains_domains(conn):
    started = datetime.datetime.now(datetime.UTC)
    interview.write_reflection_session(conn, started, ["goals", "fears"], 2, 0)
    summary = conn.execute("SELECT summary FROM reflection_sessions").fetchone()[0]
    assert "goals" in summary
    assert "fears" in summary


def test_write_reflection_session_has_timestamps(conn):
    started = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=10)
    interview.write_reflection_session(conn, started, ["beliefs"], 1, 0)
    row = conn.execute("SELECT started_at, ended_at FROM reflection_sessions").fetchone()
    assert row[0] is not None
    assert row[1] is not None


def test_write_reflection_session_zero_attrs_is_valid(conn):
    started = datetime.datetime.now(datetime.UTC)
    interview.write_reflection_session(conn, started, [], 0, 0)
    count = conn.execute("SELECT count(*) FROM reflection_sessions").fetchone()[0]
    assert count == 1


def test_write_reflection_session_multiple_sessions(conn):
    started = datetime.datetime.now(datetime.UTC)
    interview.write_reflection_session(conn, started, ["personality"], 2, 0)
    interview.write_reflection_session(conn, started, ["values"], 1, 0)
    count = conn.execute("SELECT count(*) FROM reflection_sessions").fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# call_ollama — mocked HTTP
# ---------------------------------------------------------------------------

def test_call_ollama_sends_correct_model(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = VALID_OLLAMA_RESPONSE
    captured = {}

    def mock_post(url, json=None, timeout=None):
        assert json is not None
        captured["payload"] = json
        return mock_resp

    monkeypatch.setattr(llm_router.requests, "post", mock_post)
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Q\n\nA"}]
    _call_ollama(messages, "llama3.1:8b")
    assert captured["payload"]["model"] == "llama3.1:8b"


def test_call_ollama_includes_system_prompt(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = VALID_OLLAMA_RESPONSE
    captured = {}

    def mock_post(url, json=None, timeout=None):
        assert json is not None
        captured["messages"] = json["messages"]
        return mock_resp

    monkeypatch.setattr(llm_router.requests, "post", mock_post)
    messages = [
        {"role": "system", "content": "structured data extractor"},
        {"role": "user", "content": "Q\n\nA"},
    ]
    _call_ollama(messages, "llama3.1:8b")
    assert captured["messages"][0]["role"] == "system"
    assert "structured data extractor" in captured["messages"][0]["content"]


def test_call_ollama_includes_question_and_answer(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = VALID_OLLAMA_RESPONSE
    captured = {}

    def mock_post(url, json=None, timeout=None):
        assert json is not None
        captured["messages"] = json["messages"]
        return mock_resp

    monkeypatch.setattr(llm_router.requests, "post", mock_post)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user",
         "content": "Question: What is your goal?\n\nAnswer: Build something meaningful."},
    ]
    _call_ollama(messages, "llama3.1:8b")
    user_content = captured["messages"][1]["content"]
    assert "What is your goal?" in user_content
    assert "Build something meaningful." in user_content


def test_call_ollama_returns_content_string(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = VALID_OLLAMA_RESPONSE

    monkeypatch.setattr(llm_router.requests, "post", MagicMock(return_value=mock_resp))
    messages = [{"role": "user", "content": "Q\n\nA"}]
    result = _call_ollama(messages, "llama3.1:8b")
    assert isinstance(result, str)
    assert "recharge_style" in result


def test_call_ollama_uses_configured_timeout(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = VALID_OLLAMA_RESPONSE
    captured = {}

    def mock_post(url, json=None, timeout=None):
        captured["timeout"] = timeout
        return mock_resp

    monkeypatch.setattr(llm_router.requests, "post", mock_post)
    messages = [{"role": "user", "content": "Q\n\nA"}]
    _call_ollama(messages, "llama3.1:8b")
    assert captured["timeout"] == OLLAMA_TIMEOUT


# ---------------------------------------------------------------------------
# confirm_attributes — stdin simulation
# ---------------------------------------------------------------------------

def _two_attrs():
    return [
        {"label": "recharge_style", "value": "Solitude.", "elaboration": None,
         "mutability": "stable", "confidence": 0.85},
        {"label": "decision_style", "value": "Analytical.", "elaboration": "Uses pros/cons.",
         "mutability": "evolving", "confidence": 0.7},
    ]


def test_confirm_enter_confirms_all(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _="": "")
    confirmed, retry = interview.confirm_attributes(_two_attrs())
    assert len(confirmed) == 2
    assert retry is False


def test_confirm_s_skips_question(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _="": "s")
    confirmed, retry = interview.confirm_attributes(_two_attrs())
    assert confirmed is None
    assert retry is False


def test_confirm_r_signals_retry(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _="": "r")
    confirmed, retry = interview.confirm_attributes(_two_attrs())
    assert confirmed == []
    assert retry is True


def test_confirm_skip_one_by_number(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _="": "2")
    confirmed, retry = interview.confirm_attributes(_two_attrs())
    assert len(confirmed) == 1
    assert confirmed[0]["label"] == "recharge_style"


def test_confirm_skip_all_by_number(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _="": "1,2")
    confirmed, retry = interview.confirm_attributes(_two_attrs())
    assert confirmed == []
    assert retry is False


def test_confirm_skip_first_keeps_second(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _="": "1")
    confirmed, retry = interview.confirm_attributes(_two_attrs())
    assert len(confirmed) == 1
    assert confirmed[0]["label"] == "decision_style"


def test_confirm_edit_updates_value(monkeypatch):
    """e1 → new value → Enter confirms."""
    responses = iter(["e1", "Updated value.", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(responses))
    attrs = _two_attrs()
    confirmed, retry = interview.confirm_attributes(attrs)
    assert confirmed[0]["value"] == "Updated value."
    assert retry is False


def test_confirm_edit_empty_preserves_original(monkeypatch):
    """e1 → empty → Enter keeps original value."""
    responses = iter(["e1", "", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(responses))
    attrs = _two_attrs()
    confirmed, retry = interview.confirm_attributes(attrs)
    assert confirmed[0]["value"] == "Solitude."


def test_confirm_edit_out_of_range_loops(monkeypatch):
    """e9 on a 2-attr list is invalid — should loop and then accept Enter."""
    responses = iter(["e9", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(responses))
    confirmed, retry = interview.confirm_attributes(_two_attrs())
    assert len(confirmed) == 2


# ---------------------------------------------------------------------------
# DOMAINS constant — structural correctness
# ---------------------------------------------------------------------------

def test_domains_has_eight_entries():
    assert len(interview.DOMAINS) == 8


def test_all_required_domain_names_present():
    names = {d["name"] for d in interview.DOMAINS}
    assert names == {"personality", "values", "goals", "patterns",
                     "voice", "relationships", "fears", "beliefs"}


def test_domains_appear_in_correct_order():
    names = [d["name"] for d in interview.DOMAINS]
    assert names == ["personality", "values", "goals", "patterns",
                     "voice", "relationships", "fears", "beliefs"]


def test_personality_has_seven_questions():
    d = next(d for d in interview.DOMAINS if d["name"] == "personality")
    assert len(d["questions"]) == 7


def test_values_has_four_questions():
    d = next(d for d in interview.DOMAINS if d["name"] == "values")
    assert len(d["questions"]) == 4


def test_goals_has_four_questions():
    d = next(d for d in interview.DOMAINS if d["name"] == "goals")
    assert len(d["questions"]) == 4


def test_patterns_has_five_questions():
    d = next(d for d in interview.DOMAINS if d["name"] == "patterns")
    assert len(d["questions"]) == 5


def test_voice_has_three_questions():
    d = next(d for d in interview.DOMAINS if d["name"] == "voice")
    assert len(d["questions"]) == 3


def test_relationships_has_four_questions():
    d = next(d for d in interview.DOMAINS if d["name"] == "relationships")
    assert len(d["questions"]) == 4


def test_fears_has_three_questions():
    d = next(d for d in interview.DOMAINS if d["name"] == "fears")
    assert len(d["questions"]) == 3


def test_beliefs_has_four_questions():
    d = next(d for d in interview.DOMAINS if d["name"] == "beliefs")
    assert len(d["questions"]) == 4


def test_first_personality_question_exact_wording():
    d = next(d for d in interview.DOMAINS if d["name"] == "personality")
    assert d["questions"][0] == "How do you recharge after a demanding day or week?"


def test_first_values_question_exact_wording():
    d = next(d for d in interview.DOMAINS if d["name"] == "values")
    assert d["questions"][0] == "What are the two or three things you would never compromise on?"


def test_first_beliefs_question_exact_wording():
    d = next(d for d in interview.DOMAINS if d["name"] == "beliefs")
    assert d["questions"][0] == "What do you believe separates good engineers from great ones?"


def test_every_domain_has_description():
    for d in interview.DOMAINS:
        assert d.get("description"), f"{d['name']} is missing a description"


def test_every_domain_has_at_least_one_question():
    for d in interview.DOMAINS:
        assert d["questions"], f"{d['name']} has no questions"


# ---------------------------------------------------------------------------
# _run_with_elapsed
# ---------------------------------------------------------------------------

def test_run_with_elapsed_returns_result():
    result = interview._run_with_elapsed("Working...", lambda: 42)
    assert result == 42


def test_run_with_elapsed_returns_none():
    result = interview._run_with_elapsed("Working...", lambda: None)
    assert result is None


def test_run_with_elapsed_reraises_generic_exception():
    def bad():
        raise ValueError("something broke")
    with pytest.raises(ValueError, match="something broke"):
        interview._run_with_elapsed("Working...", bad)


def test_run_with_elapsed_preserves_timeout_exception_type():
    """Timeout must propagate as its original type."""
    def timeout():
        raise requests.exceptions.Timeout()
    with pytest.raises(requests.exceptions.Timeout):
        interview._run_with_elapsed("Working...", timeout)


def test_run_with_elapsed_clears_status_line(capsys):
    """After completion the status line must be erased (cursor returned to col 0)."""
    interview._run_with_elapsed("Status", lambda: None)
    out = capsys.readouterr().out
    # The erase sequence ends with \r, leaving the cursor at the start of the line.
    assert out.endswith("\r")


# ---------------------------------------------------------------------------
# interview_question — full flow with mocks
# ---------------------------------------------------------------------------

def test_interview_question_saves_on_confirm(conn, monkeypatch, mock_get_connection):
    """Answer + confirm-all writes one attribute to the DB."""
    monkeypatch.setattr(
        interview.PrivacyBroker,
        "extract_interview_attributes",
        lambda self, q, a, task_type="interview_extraction": SimpleNamespace(
            content=[SAMPLE_ATTR],
            metadata=SimpleNamespace(task_type=task_type),
        ),
    )
    monkeypatch.setattr(interview, "get_connection", mock_get_connection)
    responses = iter(["My answer.", ""])  # answer, then Enter to confirm
    monkeypatch.setattr("builtins.input", lambda _="": next(responses))

    created, updated = interview.interview_question(
        "How do you recharge?", "personality", _local_config()
    )

    assert created == 1
    assert updated == 0
    row = conn.execute(
        "SELECT label, source, routing FROM attributes WHERE label = 'recharge_style'"
    ).fetchone()
    assert row is not None
    assert row[1] == "reflection"
    assert row[2] == "local_only"


def test_interview_question_empty_answer_skips(conn, monkeypatch, mock_get_connection):
    monkeypatch.setattr(interview, "get_connection", mock_get_connection)
    monkeypatch.setattr("builtins.input", lambda _="": "")

    created, updated = interview.interview_question("Q?", "personality", _local_config())

    assert created == 0
    assert updated == 0
    count = conn.execute("SELECT count(*) FROM attributes").fetchone()[0]
    assert count == 0


def test_interview_question_skip_at_preview_writes_nothing(conn, monkeypatch, mock_get_connection):
    monkeypatch.setattr(
        interview.PrivacyBroker,
        "extract_interview_attributes",
        lambda self, q, a, task_type="interview_extraction": SimpleNamespace(
            content=[SAMPLE_ATTR],
            metadata=SimpleNamespace(task_type=task_type),
        ),
    )
    monkeypatch.setattr(interview, "get_connection", mock_get_connection)
    responses = iter(["Some answer.", "s"])
    monkeypatch.setattr("builtins.input", lambda _="": next(responses))

    created, updated = interview.interview_question("Q?", "personality", _local_config())

    assert created == 0
    assert conn.execute("SELECT count(*) FROM attributes").fetchone()[0] == 0


def test_interview_question_supersedes_existing_on_update(conn, monkeypatch, mock_get_connection):
    """If an active attribute exists and user confirms update, old is superseded."""
    domain_id = interview.get_domain_id(conn, "personality")
    _insert_raw_attribute(conn, domain_id, "recharge_style", value="old value")

    monkeypatch.setattr(
        interview.PrivacyBroker,
        "extract_interview_attributes",
        lambda self, q, a, task_type="interview_extraction": SimpleNamespace(
            content=[SAMPLE_ATTR],
            metadata=SimpleNamespace(task_type=task_type),
        ),
    )
    monkeypatch.setattr(interview, "get_connection", mock_get_connection)
    # answer, then Enter (confirm all at preview), then 'y' to update existing
    responses = iter(["New answer.", "", "y"])
    monkeypatch.setattr("builtins.input", lambda _="": next(responses))

    created, updated = interview.interview_question(
        "How do you recharge?", "personality", _local_config()
    )

    assert updated == 1
    assert created == 0
    active = conn.execute(
        "SELECT value FROM attributes WHERE label = 'recharge_style' AND status = 'active'"
    ).fetchone()
    assert active[0] == SAMPLE_ATTR["value"]


def test_interview_question_skip_existing_on_no(conn, monkeypatch, mock_get_connection):
    """If existing attribute found and user says 'n', attribute is skipped."""
    domain_id = interview.get_domain_id(conn, "personality")
    _insert_raw_attribute(conn, domain_id, "recharge_style", value="original")

    monkeypatch.setattr(
        interview.PrivacyBroker,
        "extract_interview_attributes",
        lambda self, q, a, task_type="interview_extraction": SimpleNamespace(
            content=[SAMPLE_ATTR],
            metadata=SimpleNamespace(task_type=task_type),
        ),
    )
    monkeypatch.setattr(interview, "get_connection", mock_get_connection)
    responses = iter(["New answer.", "", "n"])  # answer, confirm, don't update
    monkeypatch.setattr("builtins.input", lambda _="": next(responses))

    created, updated = interview.interview_question(
        "How do you recharge?", "personality", _local_config()
    )

    assert created == 0
    assert updated == 0
    # Original value should be unchanged and still active
    row = conn.execute(
        "SELECT value, status FROM attributes WHERE label = 'recharge_style'"
    ).fetchone()
    assert row[0] == "original"
    assert row[1] == "active"


def test_interview_question_extraction_error_writes_nothing(conn, monkeypatch, mock_get_connection):
    """If extraction raises ExtractionError, no attribute is written."""
    from config.llm_router import ExtractionError
    monkeypatch.setattr(
        interview.PrivacyBroker,
        "extract_interview_attributes",
        lambda self, q, a, task_type="interview_extraction": (_ for _ in ()).throw(
            ExtractionError("bad json: []")
        ),
    )
    monkeypatch.setattr(interview, "get_connection", mock_get_connection)
    responses = iter(["My answer.", "n"])  # answer, then 'n' to not retry
    monkeypatch.setattr("builtins.input", lambda _="": next(responses))

    created, updated = interview.interview_question("Q?", "personality", _local_config())

    assert created == 0
    assert conn.execute("SELECT count(*) FROM attributes").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# _ollama_is_running
# ---------------------------------------------------------------------------

def test_ollama_is_running_true_when_reachable(monkeypatch):
    # MagicMock accepts any call signature, including timeout= kwargs
    monkeypatch.setattr(llm_router.requests, "get", MagicMock(return_value=MagicMock()))
    assert _ollama_is_running() is True


def test_ollama_is_running_false_on_connection_error(monkeypatch):
    monkeypatch.setattr(llm_router.requests, "get",
                        MagicMock(side_effect=requests.exceptions.ConnectionError()))
    assert _ollama_is_running() is False


def test_ollama_is_running_false_on_timeout(monkeypatch):
    monkeypatch.setattr(llm_router.requests, "get",
                        MagicMock(side_effect=requests.exceptions.Timeout()))
    assert _ollama_is_running() is False


def test_ollama_is_running_false_on_any_exception(monkeypatch):
    monkeypatch.setattr(llm_router.requests, "get",
                        MagicMock(side_effect=OSError("refused")))
    assert _ollama_is_running() is False


def test_ollama_is_running_uses_2s_timeout(monkeypatch):
    captured = {}

    def mock_get(url, timeout=None):
        captured["timeout"] = timeout
        return MagicMock()
    monkeypatch.setattr(llm_router.requests, "get", mock_get)
    _ollama_is_running()
    assert captured["timeout"] == 2


# ---------------------------------------------------------------------------
# _start_ollama
# Note: _start_ollama always attempts Popen — it does NOT check if Ollama is
# already running first. That guard lives in _ensure_local_model.
# ---------------------------------------------------------------------------

def _monotonic_time(start=0.0, step=1.0):
    """Return a callable that yields start, start+step, start+2*step, ..."""
    t = [start]

    def _next():
        v = t[0]
        t[0] += step
        return v
    return _next


def test_start_ollama_returns_process_when_ollama_responds(monkeypatch):
    """_start_ollama returns the Popen process when Ollama answers on first poll."""
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: True)
    mock_proc = MagicMock()
    monkeypatch.setattr(llm_router.subprocess, "Popen", MagicMock(return_value=mock_proc))
    monkeypatch.setattr(llm_router.time, "sleep", lambda _: None)
    monkeypatch.setattr(llm_router.time, "time", _monotonic_time())

    result = _start_ollama()
    assert result is mock_proc


def test_start_ollama_starts_process_when_not_running(monkeypatch):
    """Returns the process after a single failed poll followed by a passing poll."""
    results = iter([False, True])
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: next(results))

    mock_proc = MagicMock()
    mock_popen = MagicMock(return_value=mock_proc)
    monkeypatch.setattr(llm_router.subprocess, "Popen", mock_popen)
    monkeypatch.setattr(llm_router.time, "sleep", lambda _: None)
    monkeypatch.setattr(llm_router.time, "time", _monotonic_time())

    result = _start_ollama()
    assert result is mock_proc
    assert mock_popen.called


def test_start_ollama_popen_args(monkeypatch):
    """Popen must be called with ['ollama', 'serve'] in a new session."""
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: True)

    captured = {}

    def mock_popen(args, **kwargs):
        captured["args"] = args
        captured["start_new_session"] = kwargs.get("start_new_session", False)
        return MagicMock()
    monkeypatch.setattr(llm_router.subprocess, "Popen", mock_popen)
    monkeypatch.setattr(llm_router.time, "sleep", lambda _: None)
    monkeypatch.setattr(llm_router.time, "time", _monotonic_time())

    _start_ollama()
    assert captured["args"] == ["ollama", "serve"]
    assert captured["start_new_session"] is True


def test_start_ollama_writes_log_to_correct_path(monkeypatch, tmp_path):
    """When log_path is provided, stdout/stderr are directed to that file."""
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: True)

    log_path = tmp_path / "ollama.log"
    opened_paths = []
    real_open = open

    def mock_open(path, mode="r", **kwargs):
        opened_paths.append(str(path))
        return real_open(path, mode, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)
    monkeypatch.setattr(llm_router.subprocess, "Popen", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(llm_router.time, "sleep", lambda _: None)
    monkeypatch.setattr(llm_router.time, "time", _monotonic_time())

    _start_ollama(log_path=log_path)
    assert str(log_path) in opened_paths


def test_start_ollama_returns_none_if_not_available_in_time(monkeypatch):
    """If Ollama doesn't respond within the deadline, terminate and return None."""
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: False)
    mock_proc = MagicMock()
    monkeypatch.setattr(llm_router.subprocess, "Popen", MagicMock(return_value=mock_proc))
    monkeypatch.setattr(llm_router.time, "sleep", lambda _: None)
    # step=20 means second call to time.time() returns 20.0, past the 15s deadline
    monkeypatch.setattr(llm_router.time, "time", _monotonic_time(step=20.0))

    result = _start_ollama()
    assert result is None
    mock_proc.terminate.assert_called_once()


def test_start_ollama_returns_none_if_ollama_command_missing(monkeypatch):
    monkeypatch.setattr(llm_router.subprocess, "Popen",
                        MagicMock(side_effect=FileNotFoundError("ollama not found")))

    result = _start_ollama()
    assert result is None


# ---------------------------------------------------------------------------
# ensure_model
# ---------------------------------------------------------------------------

def _tags_response(model_names: list) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = {"models": [{"name": n} for n in model_names]}
    return mock


def test_ensure_local_model_true_when_running_and_present(monkeypatch):
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: True)
    monkeypatch.setattr(llm_router, "_ollama_has_model", lambda m: True)
    assert _ensure_local_model("llama3.1:8b") is True


def test_ensure_local_model_pulls_when_model_missing(monkeypatch):
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: True)
    monkeypatch.setattr(llm_router, "_ollama_has_model", lambda m: False)
    monkeypatch.setattr(llm_router, "_pull_model", lambda m: True)
    assert _ensure_local_model("llama3.1:8b") is True


def test_ensure_local_model_false_when_pull_fails(monkeypatch):
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: True)
    monkeypatch.setattr(llm_router, "_ollama_has_model", lambda m: False)
    monkeypatch.setattr(llm_router, "_pull_model", lambda m: False)
    assert _ensure_local_model("llama3.1:8b") is False


def test_ensure_local_model_starts_ollama_if_not_running(monkeypatch):
    monkeypatch.setattr(llm_router, "_ollama_is_running", lambda: False)
    monkeypatch.setattr(llm_router, "_start_ollama", lambda: MagicMock())
    monkeypatch.setattr(llm_router, "_ollama_has_model", lambda m: True)
    assert _ensure_local_model("llama3.1:8b") is True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def test_main_runs_interview_with_resolved_config(monkeypatch):
    """main calls run_interview with the config from resolve_router."""
    config = _local_config()
    monkeypatch.setattr(interview, "resolve_router", lambda: config)
    monkeypatch.setattr(interview, "print_routing_report", lambda c: None)
    monkeypatch.setattr(interview, "check_database", lambda: None)
    received = []
    monkeypatch.setattr(interview, "run_interview", lambda c: received.append(c))
    monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

    interview.main()

    assert received == [config]


def test_main_exits_on_configuration_error(monkeypatch):
    """main calls sys.exit(1) when resolve_router raises ConfigurationError."""
    from config.llm_router import ConfigurationError
    monkeypatch.setattr(interview, "resolve_router",
                        lambda: (_ for _ in ()).throw(ConfigurationError("no backend")))
    monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

    with pytest.raises(SystemExit) as exc_info:
        interview.main()

    assert exc_info.value.code == 1


def test_main_checks_database_before_interview(monkeypatch):
    """check_database is called before run_interview."""
    config = _local_config()
    monkeypatch.setattr(interview, "resolve_router", lambda: config)
    monkeypatch.setattr(interview, "print_routing_report", lambda c: None)
    call_order = []
    monkeypatch.setattr(interview, "check_database", lambda: call_order.append("db"))
    monkeypatch.setattr(interview, "run_interview", lambda c: call_order.append("interview"))
    monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

    interview.main()

    assert call_order == ["db", "interview"]


def test_main_exits_if_database_check_fails(monkeypatch):
    """If check_database calls sys.exit, main propagates the SystemExit."""
    config = _local_config()
    monkeypatch.setattr(interview, "resolve_router", lambda: config)
    monkeypatch.setattr(interview, "print_routing_report", lambda c: None)
    monkeypatch.setattr(interview, "check_database", lambda: sys.exit(1))
    monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

    with pytest.raises(SystemExit):
        interview.main()
