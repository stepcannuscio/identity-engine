"""Tests for config/settings.py."""

from __future__ import annotations

from unittest.mock import patch

import keyring.errors

from config import settings


def test_get_api_key_returns_none_when_keychain_read_fails(caplog):
    with patch(
        "config.settings.keyring.get_password",
        side_effect=keyring.errors.KeyringError("keychain locked"),
    ):
        result = settings.get_api_key("anthropic")

    assert result is None
    assert "Unable to read anthropic API key from keychain" in caplog.text


def test_get_db_key_raises_runtime_error_when_keychain_read_fails():
    with patch(
        "config.settings.keyring.get_password",
        side_effect=keyring.errors.KeyringError("keychain locked"),
    ):
        try:
            settings.get_db_key()
        except RuntimeError as exc:
            assert "Database encryption key could not be read" in str(exc)
        else:
            raise AssertionError("Expected get_db_key() to raise RuntimeError")


def test_get_ui_passphrase_returns_none_when_keychain_read_fails(caplog):
    with patch(
        "config.settings.keyring.get_password",
        side_effect=keyring.errors.KeyringError("keychain locked"),
    ):
        result = settings.get_ui_passphrase()

    assert result is None
    assert "Unable to read UI passphrase from keychain" in caplog.text
