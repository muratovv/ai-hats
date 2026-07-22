"""Unit tests for broken install handling at the CLI boundary (HATS-1120)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from ai_hats.cli import main_entry
from ai_hats.cli._helpers import (
    _handle_broken_install_or_die,
    _is_broken_install_exception,
    catch_broken_install,
)
from ai_hats.constants import is_debug_mode


def test_is_broken_install_exception() -> None:
    """_is_broken_install_exception identifies import/module errors vs object errors (HATS-1132)."""
    assert _is_broken_install_exception(ImportError("cannot import name foo"))
    assert _is_broken_install_exception(AttributeError("module 'ai_hats.constants' has no attribute 'FOO'"))
    assert _is_broken_install_exception(AttributeError("partially initialized module 'ai_hats' has no attribute 'bar'"))

    # Object-level AttributeError must NOT be classified as broken install
    assert not _is_broken_install_exception(AttributeError("'AgyProvider' object has no attribute 'get_cli_launch_args'"))
    assert not _is_broken_install_exception(AttributeError("'dict' object has no attribute 'foo'"))


def test_catch_broken_install_ignores_non_module_attribute_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """catch_broken_install re-raises non-module AttributeError (HATS-1132)."""
    monkeypatch.delenv("AI_HATS_DEBUG", raising=False)
    monkeypatch.delenv("AI_HATS_VERBOSE", raising=False)
    monkeypatch.setattr(sys, "argv", ["ai-hats", "task", "ls"])

    exc = AttributeError("'AgyProvider' object has no attribute 'get_cli_launch_args'")
    with pytest.raises(AttributeError) as exc_info:
        with catch_broken_install():
            raise exc

    assert exc_info.value is exc


def test_main_entry_re_raises_non_module_attribute_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """main_entry re-raises non-module AttributeError instead of reporting broken install (HATS-1132)."""
    monkeypatch.delenv("AI_HATS_DEBUG", raising=False)
    monkeypatch.delenv("AI_HATS_VERBOSE", raising=False)
    monkeypatch.setattr(sys, "argv", ["ai-hats"])

    exc = AttributeError("'AgyProvider' object has no attribute 'get_cli_launch_args'")

    def _failing_main() -> None:
        raise exc

    with patch("ai_hats.cli._guard_self_location"), patch("ai_hats.cli.main", _failing_main):
        with pytest.raises(AttributeError) as exc_info:
            main_entry()

    assert exc_info.value is exc


def test_is_debug_mode_detects_env_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:

    """is_debug_mode returns True when env vars or debug flags are present."""
    monkeypatch.delenv("AI_HATS_DEBUG", raising=False)
    monkeypatch.delenv("AI_HATS_VERBOSE", raising=False)
    monkeypatch.setattr(sys, "argv", ["ai-hats", "self", "init"])
    assert not is_debug_mode()

    # Env var triggers
    monkeypatch.setenv("AI_HATS_DEBUG", "1")
    assert is_debug_mode()
    monkeypatch.delenv("AI_HATS_DEBUG")

    monkeypatch.setenv("AI_HATS_VERBOSE", "1")
    assert is_debug_mode()
    monkeypatch.delenv("AI_HATS_VERBOSE")

    # Flag triggers
    for flag in ("--debug", "--verbose", "-v"):
        monkeypatch.setattr(sys, "argv", ["ai-hats", flag, "self", "init"])
        assert is_debug_mode()


def test_handle_broken_install_normal_mode(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """_handle_broken_install_or_die in normal mode prints friendly error and exits 1 without traceback."""
    monkeypatch.delenv("AI_HATS_DEBUG", raising=False)
    monkeypatch.delenv("AI_HATS_VERBOSE", raising=False)
    monkeypatch.setattr(sys, "argv", ["ai-hats", "task", "ls"])

    exc = ImportError("cannot import name PROVIDER_GEMINI from 'ai_hats.constants'")
    with pytest.raises(SystemExit) as exc_info:
        _handle_broken_install_or_die(exc)

    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Inconsistent or broken ai-hats installation" in captured.err
    assert "cannot import name PROVIDER_GEMINI" in captured.err
    assert "Likely cause: package files are out of sync or corrupted." in captured.err
    assert "python -m ai_hats self update" in captured.err
    assert "Debug with: AI_HATS_DEBUG=1, AI_HATS_VERBOSE=1, --debug, --verbose, -v" in captured.err
    assert "Traceback" not in captured.err


def test_catch_broken_install_context_manager(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """catch_broken_install context manager catches ImportError and exits cleanly."""
    monkeypatch.delenv("AI_HATS_DEBUG", raising=False)
    monkeypatch.delenv("AI_HATS_VERBOSE", raising=False)
    monkeypatch.setattr(sys, "argv", ["ai-hats", "task", "ls"])

    with pytest.raises(SystemExit) as exc_info:
        with catch_broken_install():
            raise AttributeError("module 'ai_hats.constants' has no attribute 'PROVIDER_GEMINI'")

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Inconsistent or broken ai-hats installation" in captured.err
    assert "module 'ai_hats.constants' has no attribute 'PROVIDER_GEMINI'" in captured.err


def test_handle_broken_install_debug_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """_handle_broken_install_or_die in debug mode re-raises the original exception."""
    monkeypatch.setenv("AI_HATS_DEBUG", "1")
    monkeypatch.setattr(sys, "argv", ["ai-hats", "task", "ls"])

    exc = ImportError("cannot import name PROVIDER_GEMINI from 'ai_hats.constants'")
    with pytest.raises(ImportError) as exc_info:
        _handle_broken_install_or_die(exc)

    assert exc_info.value is exc


def test_main_entry_catches_import_error(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """main_entry catches ImportError/AttributeError and outputs actionable remediation."""
    monkeypatch.delenv("AI_HATS_DEBUG", raising=False)
    monkeypatch.delenv("AI_HATS_VERBOSE", raising=False)
    monkeypatch.setattr(sys, "argv", ["ai-hats"])

    def _failing_main() -> None:
        raise AttributeError("module 'ai_hats.constants' has no attribute 'PROVIDER_GEMINI'")

    with patch("ai_hats.cli._guard_self_location"), patch("ai_hats.cli.main", _failing_main):
        with pytest.raises(SystemExit) as exc_info:
            main_entry()

    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Inconsistent or broken ai-hats installation" in captured.err
    assert "module 'ai_hats.constants' has no attribute 'PROVIDER_GEMINI'" in captured.err
    assert "python -m ai_hats self update" in captured.err
    assert "Debug with: AI_HATS_DEBUG=1, AI_HATS_VERBOSE=1, --debug, --verbose, -v" in captured.err
