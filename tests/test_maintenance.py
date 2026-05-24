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

from ai_hats.cli.maintenance import (
    DOWNGRADE_REFUSAL_EXIT_CODE,
    _get_changelog,
    update,
)
from ai_hats.update_check.cache import CacheEntry
from datetime import datetime, timezone


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
    ``[sys.executable, "-m", "ai_hats._bump_internal"]`` and cwd = project_dir.
    HATS-470: entry-point moved from ``ai-hats self bump`` (CLI command
    removed) to the hidden ``_bump_internal`` module.
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
         patch("ai_hats.cli.maintenance._probe_remote_state",
               return_value=None), \
         patch("subprocess.run", side_effect=fake_run):
        # old_version = "old-v" (module-level __version__ at import time);
        # patch it so the diff is unambiguous.
        with patch("ai_hats.__version__", "old-v"):
            result = CliRunner().invoke(update, [])

    assert result.exit_code == 0, f"update failed: {result.output}"

    # Find the bump subprocess invocation among captured calls
    bump_calls = [
        c for c in captured_calls
        if len(c[0]) >= 3 and c[0][1:3] == ("-m", "ai_hats._bump_internal")
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
         patch("ai_hats.cli.maintenance._probe_remote_state",
               return_value=None), \
         patch("subprocess.run", side_effect=fake_run) as mock_run, \
         patch("ai_hats.__version__", "same-version"):
        result = CliRunner().invoke(update, [])

    assert result.exit_code == 0, f"update failed: {result.output}"
    # subprocess called for pip install + verify only — never for bump
    bump_calls = [
        c for c in mock_run.call_args_list
        if len(c.args[0]) >= 3 and tuple(c.args[0][1:3]) == ("-m", "ai_hats._bump_internal")
    ]
    assert not bump_calls, f"unexpected bump subprocess: {bump_calls}"
    # in-process bump fired exactly once
    assert mock_asm.bump.call_count == 1, \
        f"in-process bump call count: {mock_asm.bump.call_count}"


# ---------- HATS-441: refuse silent downgrade ----------


def _entry(*, ahead: int | None, behind: int | None,
           installed_sha: str = "aaaaaaa1111", latest_sha: str = "bbbbbbb2222",
           installed_label: str | None = None, latest_label: str | None = None) -> CacheEntry:
    """Build a ``CacheEntry`` with controlled ahead/behind axes for gate tests."""
    return CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha=installed_sha,
        latest_sha=latest_sha,
        remote_url="https://github.com/muratovv/ai-hats.git",
        behind=behind,
        ahead=ahead,
        installed_label=installed_label,
        latest_label=latest_label,
    )


def _invoke_update(args: list[str], *, run_check_return,
                   tmp_path: Path) -> tuple[int, str, list[tuple]]:
    """Invoke ``update`` with all heavy I/O patched; return (exit, output, subprocess calls)."""
    project = _setup_update_test_env(tmp_path)
    captured: list[tuple] = []

    def fake_run(cmd_args, **kwargs):
        captured.append((tuple(cmd_args), kwargs))
        return _make_completed(cmd_args, returncode=0, stdout="ok")

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
         patch("ai_hats.cli.maintenance._assembler") as mock_asm_factory, \
         patch("ai_hats.update_check.checker.run_check",
               return_value=run_check_return), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("ai_hats.__version__", "same-version"):
        mock_asm_factory.return_value.bump.return_value = MagicMock(
            rules=[], skills=[], errors=[],
        )
        result = CliRunner().invoke(update, args)
    return result.exit_code, result.output, captured


def _pip_called(captured: list[tuple]) -> bool:
    return any(c[0][:2] == ("pip", "install") for c in captured)


def test_update_refuses_when_installed_ahead(tmp_path: Path) -> None:
    """ahead>0, behind==0 → exit 3, no pip install, refusal message."""
    exit_code, output, captured = _invoke_update(
        [], run_check_return=_entry(ahead=2, behind=0,
                                     installed_label="v0.6.1-77",
                                     latest_label="v0.6.1-70"),
        tmp_path=tmp_path,
    )
    assert exit_code == DOWNGRADE_REFUSAL_EXIT_CODE == 3, \
        f"expected exit 3, got {exit_code}; output:\n{output}"
    assert "Refusing to downgrade" in output, f"missing refusal text:\n{output}"
    assert "--force-downgrade" in output, f"missing override hint:\n{output}"
    assert "v0.6.1-77" in output and "v0.6.1-70" in output, \
        f"refusal omits version labels:\n{output}"
    assert not _pip_called(captured), \
        f"pip install ran despite refusal: {captured}"


def test_update_refuses_when_diverged(tmp_path: Path) -> None:
    """ahead>0, behind>0 → exit 3, no pip install, 'diverged' wording."""
    exit_code, output, captured = _invoke_update(
        [], run_check_return=_entry(ahead=1, behind=3),
        tmp_path=tmp_path,
    )
    assert exit_code == DOWNGRADE_REFUSAL_EXIT_CODE, \
        f"expected exit 3, got {exit_code}; output:\n{output}"
    assert "diverged" in output.lower(), f"missing 'diverged' wording:\n{output}"
    assert "Refusing to downgrade" in output, f"missing refusal text:\n{output}"
    assert not _pip_called(captured), \
        f"pip install ran despite refusal: {captured}"


def test_update_force_downgrade_bypasses_gate(tmp_path: Path) -> None:
    """--force-downgrade with ahead state → exit 0, warning printed, pip runs."""
    exit_code, output, captured = _invoke_update(
        ["--force-downgrade"],
        run_check_return=_entry(ahead=2, behind=0),
        tmp_path=tmp_path,
    )
    assert exit_code == 0, f"expected exit 0, got {exit_code}; output:\n{output}"
    assert "--force-downgrade bypasses" in output, \
        f"missing flag warning:\n{output}"
    assert "Refusing to downgrade" not in output, \
        f"refusal printed despite override:\n{output}"
    assert _pip_called(captured), \
        f"pip install did not run with --force-downgrade: {captured}"


def test_update_proceeds_when_behind(tmp_path: Path) -> None:
    """behind>0, ahead==0 → normal update path: no refusal, pip runs."""
    exit_code, output, captured = _invoke_update(
        [], run_check_return=_entry(ahead=0, behind=4),
        tmp_path=tmp_path,
    )
    assert exit_code == 0, f"expected exit 0, got {exit_code}; output:\n{output}"
    assert "Refusing to downgrade" not in output, \
        f"unexpected refusal:\n{output}"
    assert "--force-downgrade bypasses" not in output, \
        f"unexpected flag warning without --force-downgrade:\n{output}"
    assert _pip_called(captured), f"pip install missing: {captured}"


def test_update_proceeds_when_probe_unknown(tmp_path: Path) -> None:
    """run_check returns None (non-git install / probe failure) → proceed."""
    exit_code, output, captured = _invoke_update(
        [], run_check_return=None, tmp_path=tmp_path,
    )
    assert exit_code == 0, f"expected exit 0, got {exit_code}; output:\n{output}"
    assert "Refusing to downgrade" not in output, \
        f"unexpected refusal on unknown probe:\n{output}"
    assert _pip_called(captured), f"pip install missing: {captured}"


def test_update_skips_pip_when_installed_sha_matches_remote(tmp_path: Path) -> None:
    """installed_sha == latest_sha → pip install short-circuited, bump still runs.

    HATS-follow-up: when ``run_check`` confirms the installed SHA matches
    remote ``master``, the unconditional ``pip install --force-reinstall
    --no-cache-dir`` is a 10-15s no-op (~minute on slow links) that users
    have mistaken for a hang. Reuse the probe entry to short-circuit pip;
    bump still runs so any pending in-process migrations apply.
    """
    same_sha = "deadbeefcafe1234"
    exit_code, output, captured = _invoke_update(
        [],
        run_check_return=_entry(
            ahead=0, behind=0,
            installed_sha=same_sha, latest_sha=same_sha,
        ),
        tmp_path=tmp_path,
    )
    assert exit_code == 0, f"expected exit 0, got {exit_code}; output:\n{output}"
    assert not _pip_called(captured), \
        f"pip install ran despite SHA match: {captured}"
    assert "Already up to date" in output, \
        f"missing already-up-to-date banner:\n{output}"
    assert "skipping pip install" in output, \
        f"missing skip-pip dim hint:\n{output}"
    # Verify subprocess must not run when pip was skipped — nothing to verify.
    verify_called = any(
        len(c[0]) >= 4 and c[0][1:4] == ("-m", "ai_hats._bootstrap")
        for c in captured
    )
    assert not verify_called, \
        f"_bootstrap verify ran despite skipped install: {captured}"


def test_update_proceeds_when_ahead_behind_axes_none(tmp_path: Path) -> None:
    """run_check returns entry with ahead=None or behind=None (no git fetch).

    Defensive: HATS-432 sets these to None when ``git fetch`` into pkg dir
    fails (non-git install, network down). Gate MUST NOT fire — fall back to
    current behaviour.
    """
    exit_code, output, captured = _invoke_update(
        [], run_check_return=_entry(ahead=None, behind=None),
        tmp_path=tmp_path,
    )
    assert exit_code == 0, f"expected exit 0, got {exit_code}; output:\n{output}"
    assert "Refusing to downgrade" not in output, \
        f"unexpected refusal on unresolved axes:\n{output}"
    assert _pip_called(captured), f"pip install missing: {captured}"
