"""Tests for local security posture parsing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.security_posture import (
    parse_auto_login_status,
    parse_filevault_status,
    parse_password_after_sleep_status,
    parse_personal_recovery_key_status,
)


def test_parse_filevault_status():
    assert parse_filevault_status("FileVault is On.") == "enabled"
    assert parse_filevault_status("FileVault is Off.") == "disabled"
    assert parse_filevault_status("n/a") == "unknown"


def test_parse_personal_recovery_key_status():
    assert parse_personal_recovery_key_status("true") == "enabled"
    assert parse_personal_recovery_key_status("false") == "disabled"
    assert parse_personal_recovery_key_status("") == "unknown"


def test_parse_auto_login_status():
    assert parse_auto_login_status("", 1) == "enabled"
    assert parse_auto_login_status("step", 0) == "disabled"


def test_parse_password_after_sleep_status():
    assert parse_password_after_sleep_status("1", "0") == "enabled"
    assert parse_password_after_sleep_status("0", "300") == "disabled"
    assert parse_password_after_sleep_status("x", "0") == "unknown"
