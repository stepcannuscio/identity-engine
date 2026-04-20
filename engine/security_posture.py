"""Read-only local machine security posture checks."""

from __future__ import annotations

from dataclasses import dataclass
import platform
import subprocess
from typing import Any, cast


@dataclass(frozen=True)
class SecurityCheck:
    """One local security recommendation and its observed status."""

    code: str
    label: str
    status: str
    recommended_value: str
    action_required: bool
    summary: str
    recommendation: str


def get_security_check_overrides(conn) -> dict[str, bool]:
    """Return persisted user confirmations for security checks."""
    rows = conn.execute(
        """
        SELECT check_code, is_complete
        FROM security_check_overrides
        """
    ).fetchall()
    return {
        str(row[0]): bool(row[1])
        for row in rows
    }


def set_security_check_override(conn, check_code: str, *, is_complete: bool) -> None:
    """Persist or clear a manual completion override for one check."""
    if not is_complete:
        conn.execute(
            "DELETE FROM security_check_overrides WHERE check_code = ?",
            (check_code,),
        )
        conn.commit()
        return

    conn.execute(
        """
        INSERT INTO security_check_overrides (check_code, is_complete)
        VALUES (?, 1)
        ON CONFLICT(check_code) DO UPDATE SET
            is_complete = 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (check_code,),
    )
    conn.commit()


def _run(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode, output
    except Exception:
        return 1, ""


def parse_filevault_status(output: str) -> str:
    lowered = output.lower()
    if "filevault is on" in lowered:
        return "enabled"
    if "filevault is off" in lowered:
        return "disabled"
    return "unknown"


def parse_personal_recovery_key_status(output: str) -> str:
    lowered = output.strip().lower()
    if lowered in {"true", "yes"}:
        return "enabled"
    if lowered in {"false", "no"}:
        return "disabled"
    return "unknown"


def parse_auto_login_status(output: str, return_code: int) -> str:
    if return_code != 0:
        return "enabled"
    if output.strip():
        return "disabled"
    return "enabled"


def parse_password_after_sleep_status(ask_output: str, delay_output: str) -> str:
    try:
        ask_value = int(str(ask_output).strip())
        delay_value = int(float(str(delay_output).strip()))
    except ValueError:
        return "unknown"
    if ask_value == 1 and delay_value == 0:
        return "enabled"
    return "disabled"


def inspect_security_posture() -> dict[str, object]:
    """Return a privacy-safe snapshot of recommended machine security settings."""
    system = platform.system()
    if system != "Darwin":
        return {
            "platform": system.lower(),
            "supported": False,
            "checks": [
                {
                    "code": "manual_review",
                    "label": "Security settings review",
                    "status": "unknown",
                    "summary": "Automatic inspection is only implemented for macOS right now.",
                    "recommendation": "Review disk encryption, screen lock, and boot login settings manually.",
                }
            ],
        }

    filevault_rc, filevault_output = _run(["fdesetup", "status"])
    recovery_rc, recovery_output = _run(["fdesetup", "haspersonalrecoverykey"])
    auto_login_rc, auto_login_output = _run(
        ["defaults", "read", "/Library/Preferences/com.apple.loginwindow", "autoLoginUser"]
    )
    ask_rc, ask_output = _run(
        ["defaults", "-currentHost", "read", "com.apple.screensaver", "askForPassword"]
    )
    delay_rc, delay_output = _run(
        ["defaults", "-currentHost", "read", "com.apple.screensaver", "askForPasswordDelay"]
    )

    password_status = "unknown"   # pragma: allowlist secret
    if ask_rc == 0 and delay_rc == 0:
        password_status = parse_password_after_sleep_status(ask_output, delay_output)

    checks = [
        SecurityCheck(
            code="filevault",
            label="FileVault disk encryption",
            recommended_value="Enabled with a personal recovery key stored locally.",
            action_required=parse_filevault_status(filevault_output) == "disabled" if filevault_rc == 0 else True,
            status=parse_filevault_status(filevault_output) if filevault_rc == 0 else "unknown",
            summary="Protect local identity data at rest with full-disk encryption.",
            recommendation="Enable FileVault and use a personal/local recovery key stored offline in a safe place.",
        ),
        SecurityCheck(
            code="personal_recovery_key",
            label="Personal recovery key",
            recommended_value="Enabled.",
            action_required=(
                parse_personal_recovery_key_status(recovery_output) != "enabled"
                if recovery_rc == 0
                else True
            ),
            status=(
                parse_personal_recovery_key_status(recovery_output)
                if recovery_rc == 0
                else "unknown"
            ),
            summary="A personal recovery key keeps recovery under your control.",
            recommendation="Prefer a personal/local recovery key over shared or escrow-only recovery when possible.",
        ),
        SecurityCheck(
            code="password_after_sleep",
            label="Immediate password after sleep",
            recommended_value="Enabled with zero delay.",
            action_required=password_status != "enabled",  # pragma: allowlist secret
            status=password_status,
            summary="Require a password immediately when the Mac sleeps or the screen saver starts.",
            recommendation="Turn on immediate password requirement after sleep or screen saver.",
        ),
        SecurityCheck(
            code="auto_login",
            label="Login required at boot",
            recommended_value="Auto-login disabled.",
            action_required=parse_auto_login_status(auto_login_output, auto_login_rc) != "enabled",
            status=parse_auto_login_status(auto_login_output, auto_login_rc),
            summary="Automatic login weakens physical access protections.",
            recommendation="Disable auto-login so a password is required after boot.",
        ),
    ]

    return {
        "platform": "macos",
        "supported": True,
        "checks": [
            {
                "code": check.code,
                "label": check.label,
                "status": check.status,
                "recommended_value": check.recommended_value,
                "action_required": check.action_required,
                "summary": check.summary,
                "recommendation": check.recommendation,
            }
            for check in checks
        ],
    }


def apply_security_check_overrides(
    posture: dict[str, object],
    overrides: dict[str, bool],
) -> dict[str, object]:
    """Merge persisted user confirmations into the current posture snapshot."""
    resolved_checks: list[dict[str, object]] = []
    raw_checks = cast(list[dict[str, Any]], posture.get("checks", []))
    for raw_check in raw_checks:
        if not isinstance(raw_check, dict):
            continue
        check = dict(raw_check)
        user_marked_complete = bool(overrides.get(str(check.get("code", "")), False))
        check["user_marked_complete"] = user_marked_complete
        if user_marked_complete:
            check["action_required"] = False
        resolved_checks.append(check)

    return {
        "platform": posture["platform"],
        "supported": posture["supported"],
        "checks": resolved_checks,
    }


def resolve_security_posture(conn) -> dict[str, object]:
    """Return the current posture merged with persisted manual confirmations."""
    return apply_security_check_overrides(
        inspect_security_posture(),
        get_security_check_overrides(conn),
    )
