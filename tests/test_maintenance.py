"""Unit tests for ``cli.maintenance`` helpers (HATS-398).

Currently covers ``_get_changelog``: shallow-clones the public repo and
returns recent commits formatted for the ``Recent changes`` block of
``ai-hats self update``. The contract: hide merge commits, keep
conventional-commit titles.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from ai_hats.cli.maintenance import _get_changelog


def _make_completed(args, *, returncode: int, stdout: str = "", stderr: str = ""):
    """Build a ``CompletedProcess`` for ``subprocess.run`` mock."""
    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout=stdout, stderr=stderr,
    )


def test_get_changelog_passes_no_merges_flag() -> None:
    """``git log`` invocation must include ``--no-merges`` to hide merge titles."""
    captured_args: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured_args.append(args)
        # First call = git clone (succeeds), second = git log
        if "clone" in args:
            return _make_completed(args, returncode=0)
        return _make_completed(args, returncode=0, stdout="abc1234 fix(x): y\n")

    with patch("subprocess.run", side_effect=fake_run):
        _get_changelog()

    # Two subprocess calls: clone, then log
    assert len(captured_args) == 2
    log_args = captured_args[1]
    assert "log" in log_args
    assert "--no-merges" in log_args, \
        f"expected --no-merges in git log args, got: {log_args}"


def test_get_changelog_returns_log_output_on_success() -> None:
    """Successful flow returns stripped stdout of ``git log``."""
    def fake_run(args, **kwargs):
        if "clone" in args:
            return _make_completed(args, returncode=0)
        return _make_completed(
            args, returncode=0,
            stdout="abc1234 fix(self-bump): y\ndef5678 feat(x): z\n",
        )

    with patch("subprocess.run", side_effect=fake_run):
        out = _get_changelog()
    assert "abc1234 fix(self-bump): y" in out
    assert "def5678 feat(x): z" in out


def test_get_changelog_returns_empty_when_clone_fails() -> None:
    def fake_run(args, **kwargs):
        return _make_completed(args, returncode=128, stderr="ssh denied")

    with patch("subprocess.run", side_effect=fake_run):
        assert _get_changelog() == ""


def test_get_changelog_returns_empty_when_log_fails() -> None:
    def fake_run(args, **kwargs):
        if "clone" in args:
            return _make_completed(args, returncode=0)
        return _make_completed(args, returncode=1)

    with patch("subprocess.run", side_effect=fake_run):
        assert _get_changelog() == ""


def test_get_changelog_handles_subprocess_error() -> None:
    """Network / OS issues during clone surface as empty string, not raise."""
    def fake_run(args, **kwargs):
        raise subprocess.SubprocessError("timeout")

    with patch("subprocess.run", side_effect=fake_run):
        assert _get_changelog() == ""
