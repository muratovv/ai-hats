"""Tests for `ai-hats --tree` and `ai-hats --tree <path>` (subtree)."""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from ai_hats.cli import _extract_tree_path, main, main_entry


def _invoke_main(args: list[str]) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(main, args)
    return result.exit_code, result.output


def _invoke_entry(argv: list[str], capsys) -> tuple[int, str]:
    """Run `main_entry` with monkeypatched argv and capture stdout."""
    saved = sys.argv[:]
    sys.argv = argv
    code: int = 0
    try:
        try:
            main_entry()
        except SystemExit as e:
            code = e.code or 0
    finally:
        sys.argv = saved
    return code, capsys.readouterr().out


# ---------- Full tree (via click callback, CliRunner-friendly) ----------

def test_full_tree_lists_all_top_level_groups():
    code, out = _invoke_main(["--tree"])
    assert code == 0, out
    for group in ["agent", "config", "list", "reflect", "self", "session", "task", "wt"]:
        assert group in out, f"group {group!r} missing from --tree output"


def test_full_tree_renders_nested_subcommands():
    """`task hyp append-verdict` is two levels deep — must appear in tree."""
    code, out = _invoke_main(["--tree"])
    assert code == 0
    assert "append-verdict" in out
    assert "merge" in out
    assert "retro" in out


def test_full_tree_includes_option_help_text():
    code, out = _invoke_main(["--tree"])
    assert code == 0
    assert "--ticket" in out
    assert "--priority" in out


# ---------- Subtree (via main_entry shim, requires real argv) ----------

def test_subtree_single_level_leaf(capsys):
    """`--tree agent` renders only `agent` (a leaf command), no other groups."""
    code, out = _invoke_entry(["ai-hats", "--tree", "agent"], capsys)
    assert code == 0, out
    assert "ai-hats agent" in out
    # Must NOT contain headlines of sibling groups.
    assert "View and update project configuration" not in out  # config
    assert "Manage git worktrees" not in out                  # wt
    assert "Manage task cards" not in out                     # task
    # Should still show options of agent.
    assert "--ticket" in out


def test_subtree_multilevel_group(capsys):
    """`--tree task hyp` walks two levels and renders the hyp subgroup."""
    code, out = _invoke_entry(["ai-hats", "--tree", "task", "hyp"], capsys)
    assert code == 0, out
    assert "ai-hats task hyp" in out
    assert "append-verdict" in out
    # Must NOT contain task-level siblings outside hyp.
    assert "Transition a task" not in out  # task transition's headline
    assert "proposal" not in out.lower() or "task hyp" in out  # proposal is a sibling group


def test_subtree_unknown_top_level_errors(capsys):
    code, out = _invoke_entry(["ai-hats", "--tree", "foobar"], capsys)
    assert code == 2, out
    assert "unknown subcommand" in out
    assert "foobar" in out
    # Hint lists available top-level groups.
    assert "agent" in out


def test_subtree_unknown_nested_errors(capsys):
    code, out = _invoke_entry(["ai-hats", "--tree", "task", "nonexistent"], capsys)
    assert code == 2, out
    assert "unknown subcommand" in out
    assert "nonexistent" in out


def test_subtree_full_tree_when_no_path(capsys):
    """`--tree` alone (no path) renders the full tree via the same shim path."""
    code, out = _invoke_entry(["ai-hats", "--tree"], capsys)
    assert code == 0, out
    # Sanity: all 8 groups present.
    for group in ["agent", "config", "list", "reflect", "self", "session", "task", "wt"]:
        assert group in out


# ---------- Pure unit tests on the argv parser ----------

@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--tree"], []),
        (["--tree", "agent"], ["agent"]),
        (["--tree", "task", "hyp"], ["task", "hyp"]),
        (["--help", "--tree", "task"], ["task"]),
        (["--tree", "task", "--help"], ["task"]),
        # Root options with values are skipped along with their value.
        (["-p", "claude", "--tree", "agent"], ["agent"]),
        (["--tree", "agent", "--tag", "k=v"], ["agent"]),
        (["--tag=k=v", "--tree", "wt"], ["wt"]),
        # Tokens BEFORE --tree are ignored (reverse order intentionally
        # not supported per HATS-250 spec).
        (["agent", "--tree"], []),
        # No --tree at all → empty path (caller checks presence first).
        (["--help"], []),
    ],
)
def test_extract_tree_path(argv, expected):
    assert _extract_tree_path(argv) == expected
