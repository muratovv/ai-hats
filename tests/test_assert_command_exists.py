"""Tests for ``tests/_cli_helpers.py:assert_command_exists`` (HATS-374)."""

from __future__ import annotations

import pytest

from tests._cli_helpers import assert_command_exists

pytestmark = pytest.mark.integration


def test_assert_command_exists_top_level():
    """Top-level command — `ai-hats agent`."""
    assert_command_exists("agent")


def test_assert_command_exists_two_level():
    """Two-level nested command — `ai-hats self init` (the HATS-242 case)."""
    assert_command_exists("self", "init")


def test_assert_command_exists_three_level():
    """Three-level nested command — `ai-hats task hyp create`."""
    assert_command_exists("task", "hyp", "create")


def test_assert_command_exists_raises_for_missing():
    """Non-existing command path raises AssertionError with stderr in message."""
    with pytest.raises(AssertionError) as excinfo:
        assert_command_exists("self", "definitely-not-a-real-subcommand")
    message = str(excinfo.value)
    assert "self definitely-not-a-real-subcommand" in message
    assert "stderr" in message


def test_assert_command_exists_rejects_empty_path():
    """Empty path is a programming error, not a command-existence failure."""
    with pytest.raises(ValueError):
        assert_command_exists()
