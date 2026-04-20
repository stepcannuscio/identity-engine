"""Tests for local security posture parsing."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.security_posture import (
    apply_security_check_overrides,
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


def test_apply_security_check_overrides_resolves_unknown_checks_marked_complete():
    posture = {
        "platform": "macos",
        "supported": True,
        "checks": [
            {
                "code": "personal_recovery_key",
                "label": "Personal recovery key",
                "status": "unknown",
                "recommended_value": "Enabled.",
                "action_required": True,
                "summary": "A personal recovery key keeps recovery under your control.",
                "recommendation": "Prefer a personal recovery key.",
            }
        ],
    }

    resolved = apply_security_check_overrides(
        posture,
        {"personal_recovery_key": True},
    )

    checks = cast(list[dict[str, object]], resolved["checks"])
    assert checks[0]["user_marked_complete"] is True
    assert checks[0]["action_required"] is False
