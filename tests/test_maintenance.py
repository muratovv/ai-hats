"""Unit tests for ``cli.maintenance`` helpers (HATS-398, HATS-400).

- ``_get_changelog``: shallow-clones the public repo and returns recent
  commits formatted for the ``Recent changes`` block of
  ``ai-hats self update``. The contract: hide merge commits, keep
  conventional-commit titles.
- ``update`` subprocess-bump control flow (HATS-400): when the version
  on disk changes, auto-bump runs in a fresh interpreter so newly
  installed code (migrations, healer, etc.) activates without a second
  user invocation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from ai_hats.cli.maintenance import _get_changelog, update


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


# ---------- HATS-400: subprocess-bump on version change ----------


def _setup_update_test_env(tmp_path: Path) -> Path:
    """Seed a minimal project with active role so ``update`` reaches step 5."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: assistant\n"
    )
    return project


def test_update_runs_bump_in_subprocess_when_version_changed(tmp_path: Path) -> None:
    """When ``new_version != old_version``, bump runs in fresh interpreter.

    Contract: ``subprocess.run`` is called with
    ``[sys.executable, "-m", "ai_hats", "self", "bump"]`` and cwd = project_dir.
    """
    project = _setup_update_test_env(tmp_path)
    captured_calls: list[tuple] = []

    def fake_run(args, **kwargs):
        captured_calls.append((tuple(args), kwargs))
        # First call: pip install — succeed
        # Second call: verify — succeed
        # Third call: bump — succeed
        return _make_completed(args, returncode=0, stdout="ok")

    with patch("ai_hats.cli.maintenance._project_dir", return_value=project), \
         patch("ai_hats.cli.maintenance._get_installed_version",
               side_effect=["new-version-X"]), \
         patch("ai_hats.cli.maintenance._snapshot_library", return_value={}), \
         patch("ai_hats.cli.maintenance._snapshot_dep_versions", return_value={}), \
         patch("ai_hats.cli.maintenance._snapshot_composition",
               return_value=(set(), set())), \
         patch("ai_hats.cli.maintenance._format_component_diff", return_value=False), \
         patch("ai_hats.cli.maintenance._build_update_cmd",
               return_value=["pip", "install", "ai-hats"]), \
         patch("ai_hats.cli.maintenance._get_changelog", return_value=""), \
         patch("ai_hats.cli.maintenance._assembler") as mock_asm_factory, \
         patch("subprocess.run", side_effect=fake_run):
        # old_version = "old-v" (module-level __version__ at import time);
        # patch it so the diff is unambiguous.
        with patch("ai_hats.__version__", "old-v"):
            result = CliRunner().invoke(update, [])

    assert result.exit_code == 0, f"update failed: {result.output}"

    # Find the bump subprocess invocation among captured calls
    bump_calls = [
        c for c in captured_calls
        if len(c[0]) >= 4 and c[0][1:5] == ("-m", "ai_hats", "self", "bump")
    ]
    assert bump_calls, (
        f"expected subprocess bump call, got: "
        f"{[c[0] for c in captured_calls]}"
    )
    args, kwargs = bump_calls[0]
    assert args[0] == sys.executable, f"wrong interpreter: {args[0]}"
    assert kwargs.get("cwd") == str(project), f"wrong cwd: {kwargs.get('cwd')}"
    assert kwargs.get("check") is False, "must not raise on bump exit code"
    # in-process bump should NOT be called when version changed
    assert not mock_asm_factory.return_value.bump.called, \
        "in-process bump fired despite version change"


def test_update_runs_bump_in_process_when_version_unchanged(tmp_path: Path) -> None:
    """When versions match, in-process ``asm.bump()`` runs (no subprocess)."""
    project = _setup_update_test_env(tmp_path)
    bump_result_stub = MagicMock(rules=[], skills=[], errors=[])

    def fake_run(args, **kwargs):
        # pip install + verify should both succeed; no bump subprocess expected
        return _make_completed(args, returncode=0, stdout="ok")

    mock_asm = MagicMock()
    mock_asm.bump.return_value = bump_result_stub

    with patch("ai_hats.cli.maintenance._project_dir", return_value=project), \
         patch("ai_hats.cli.maintenance._get_installed_version",
               side_effect=["same-version"]), \
         patch("ai_hats.cli.maintenance._snapshot_library", return_value={}), \
         patch("ai_hats.cli.maintenance._snapshot_dep_versions", return_value={}), \
         patch("ai_hats.cli.maintenance._snapshot_composition",
               return_value=(set(), set())), \
         patch("ai_hats.cli.maintenance._format_component_diff", return_value=False), \
         patch("ai_hats.cli.maintenance._build_update_cmd",
               return_value=["pip", "install", "ai-hats"]), \
         patch("ai_hats.cli.maintenance._get_changelog", return_value=""), \
         patch("ai_hats.cli.maintenance._assembler", return_value=mock_asm), \
         patch("subprocess.run", side_effect=fake_run) as mock_run, \
         patch("ai_hats.__version__", "same-version"):
        result = CliRunner().invoke(update, [])

    assert result.exit_code == 0, f"update failed: {result.output}"
    # subprocess called for pip install + verify only — never for bump
    bump_calls = [
        c for c in mock_run.call_args_list
        if len(c.args[0]) >= 4 and tuple(c.args[0][1:5]) == ("-m", "ai_hats", "self", "bump")
    ]
    assert not bump_calls, f"unexpected bump subprocess: {bump_calls}"
    # in-process bump fired exactly once
    assert mock_asm.bump.call_count == 1, \
        f"in-process bump call count: {mock_asm.bump.call_count}"
