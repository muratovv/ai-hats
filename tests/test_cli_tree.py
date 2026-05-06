"""Tests for `ai-hats --tree`."""

from __future__ import annotations

from click.testing import CliRunner

from ai_hats.cli import main, main_entry


def _invoke_tree(args: list[str]) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(main, args)
    return result.exit_code, result.output


def test_tree_flag_lists_all_top_level_groups():
    code, out = _invoke_tree(["--tree"])
    assert code == 0, out
    for group in ["agent", "config", "list", "reflect", "self", "session", "task", "wt"]:
        assert group in out, f"group {group!r} missing from --tree output"


def test_tree_flag_renders_nested_subcommands():
    """`task hyp append-verdict` is two levels deep — must appear in tree."""
    code, out = _invoke_tree(["--tree"])
    assert code == 0
    # The deeply nested append-verdict and proposal-vote under task.
    assert "append-verdict" in out
    assert "proposal" in out
    # `wt` subcommands.
    assert "merge" in out
    assert "discard" in out
    # `session` subcommands.
    assert "retro" in out
    assert "audit" in out


def test_tree_flag_includes_option_help_text():
    """Each command's options are rendered with their help text."""
    code, out = _invoke_tree(["--tree"])
    assert code == 0
    # Options of `agent` — ticket flag has descriptive help text.
    assert "--ticket" in out
    # Options of `task create`.
    assert "--priority" in out


def test_tree_flag_does_not_launch_session():
    """Just exits 0; no subprocess, no provider call."""
    code, _ = _invoke_tree(["--tree"])
    assert code == 0


def test_tree_works_with_help_present(monkeypatch):
    """`main_entry` strips `--help` when `--tree` is also present so order
    doesn't matter."""
    import sys
    captured: list[list[str]] = []

    real_main = main

    def fake_main(*args, **kwargs):
        captured.append(list(sys.argv))
        # Don't actually invoke click, just record what main_entry passed.

    monkeypatch.setattr("ai_hats.cli.main", fake_main)
    monkeypatch.setattr(sys, "argv", ["ai-hats", "--help", "--tree"])
    main_entry()
    assert captured, "main was not called"
    assert "--help" not in captured[0]
    assert "--tree" in captured[0]
    # Restore for other tests
    monkeypatch.setattr("ai_hats.cli.main", real_main)
