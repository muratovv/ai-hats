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

import pytest
from click.testing import CliRunner

from ai_hats.cli.maintenance import (
    DOWNGRADE_REFUSAL_EXIT_CODE,
    _get_changelog,
    update,
)
from ai_hats.models import ProjectConfigError
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
        # HATS-764: pin edge so these command-level update tests exercise the
        # git ahead/diverged guard + legacy in-place path (behaviourally the
        # pre-channel default). stable/local routing is covered by dedicated
        # unit + e2e tests.
        "harness:\n"
        "  channel: edge\n"
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
               return_value=["uv", "pip", "install", "ai-hats"]), \
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
    # HATS-469: in-process pipeline (was asm.bump) should NOT be called
    # when version changed — subprocess path takes over.
    assert not mock_asm_factory.return_value._refresh.called, \
        "in-process _refresh fired despite version change"


def test_update_runs_bump_in_process_when_version_unchanged(tmp_path: Path) -> None:
    """When versions match, in-process pipeline runs (no subprocess).

    HATS-469: ``Assembler.bump`` was removed; the in-process pipeline now
    composes ``_run_v07_migration`` + ``compose_for_role`` + ``_refresh``
    + ``_run_diagnostics`` inline in ``cli/maintenance.py``. We spy on
    ``_refresh`` as the canonical "in-process pipeline fired" signal.
    """
    project = _setup_update_test_env(tmp_path)
    bump_result_stub = MagicMock(rules=[], skills=[], errors=[])

    def fake_run(args, **kwargs):
        # pip install + verify should both succeed; no bump subprocess expected
        return _make_completed(args, returncode=0, stdout="ok")

    mock_asm = MagicMock()
    # mock_asm._refresh returns None (matches real signature); the bump-result
    # equivalent is now ``compose_for_role(...)``, patched separately below.

    with patch("ai_hats.cli.maintenance._project_dir", return_value=project), \
         patch("ai_hats.cli.maintenance._get_installed_version",
               side_effect=["same-version"]), \
         patch("ai_hats.cli.maintenance._snapshot_library", return_value={}), \
         patch("ai_hats.cli.maintenance._snapshot_dep_versions", return_value={}), \
         patch("ai_hats.cli.maintenance._snapshot_composition",
               return_value=(set(), set())), \
         patch("ai_hats.cli.maintenance._format_component_diff", return_value=False), \
         patch("ai_hats.cli.maintenance._build_update_cmd",
               return_value=["uv", "pip", "install", "ai-hats"]), \
         patch("ai_hats.cli.maintenance._get_changelog", return_value=""), \
         patch("ai_hats.cli.maintenance._assembler", return_value=mock_asm), \
         patch("ai_hats.cli.maintenance._probe_remote_state",
               return_value=None), \
         patch("ai_hats.materialize.compose_for_role",
               return_value=bump_result_stub), \
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
    # in-process pipeline fired exactly once (signal = _refresh call)
    assert mock_asm._refresh.call_count == 1, \
        f"in-process _refresh call count: {mock_asm._refresh.call_count}"


# ---------- HATS-581: config-tolerant self update ----------


def _run_degraded_update(tmp_path, *, version_changed, assembler_side_effect):
    """Invoke ``update`` on the degraded path; return (result, captured_calls)."""
    project = _setup_update_test_env(tmp_path)
    installed = "new-version-X" if version_changed else "same-version"
    old_version = "old-v" if version_changed else "same-version"
    captured: list[tuple] = []

    def fake_run(args, **kwargs):
        captured.append((tuple(args), kwargs))
        return _make_completed(args, returncode=0, stdout="ok")

    with patch("ai_hats.cli.maintenance._project_dir", return_value=project), \
         patch("ai_hats.cli.maintenance._get_installed_version",
               side_effect=[installed]), \
         patch("ai_hats.cli.maintenance._snapshot_library", return_value={}), \
         patch("ai_hats.cli.maintenance._snapshot_dep_versions", return_value={}), \
         patch("ai_hats.cli.maintenance._snapshot_composition",
               return_value=(set(), set())), \
         patch("ai_hats.cli.maintenance._format_component_diff", return_value=False), \
         patch("ai_hats.cli.maintenance._build_update_cmd",
               return_value=["uv", "pip", "install", "ai-hats"]), \
         patch("ai_hats.cli.maintenance._get_changelog", return_value=""), \
         patch("ai_hats.cli.maintenance._assembler",
               side_effect=assembler_side_effect), \
         patch("ai_hats.cli.maintenance._probe_remote_state", return_value=None), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("ai_hats.__version__", old_version):
        result = CliRunner().invoke(update, [])
    return result, captured


def _bump_subprocess_calls(captured: list[tuple]) -> list[tuple]:
    return [
        c for c in captured
        if len(c[0]) >= 3 and c[0][1:3] == ("-m", "ai_hats._bump_internal")
    ]


def test_update_degrades_on_unparseable_config(tmp_path: Path) -> None:
    """HATS-581 Fix #1: a config the INSTALLED code can't parse must not block
    ``self update``. The pre-install read raises ProjectConfigError; update
    degrades, installs, and forces the fresh-interpreter bump to heal it."""
    mock_asm = MagicMock()
    result, captured = _run_degraded_update(
        tmp_path,
        version_changed=True,
        # 1st call (pre-install) raises; 2nd (post-bump re-read) succeeds.
        assembler_side_effect=[ProjectConfigError("unknown key 'x'"), mock_asm],
    )

    assert result.exit_code == 0, f"update crashed: {result.output}"
    assert "not parseable by the installed version" in result.output
    assert _bump_subprocess_calls(captured), (
        f"expected heal bump subprocess, got: {[c[0] for c in captured]}"
    )


def test_update_forces_subprocess_bump_when_config_unreadable(tmp_path: Path) -> None:
    """HATS-581 Fix #1: even when versions match, an unreadable config forces
    the SUBPROCESS bump (in-process would re-run the un-parsing code)."""
    mock_asm = MagicMock()
    result, captured = _run_degraded_update(
        tmp_path,
        version_changed=False,
        assembler_side_effect=[ProjectConfigError("unknown key 'x'"), mock_asm],
    )

    assert result.exit_code == 0, f"update crashed: {result.output}"
    assert _bump_subprocess_calls(captured), (
        "config_unreadable must force the subprocess bump even with no "
        f"version change; calls: {[c[0] for c in captured]}"
    )
    # in-process pipeline must NOT have fired
    assert not mock_asm._refresh.called, "in-process _refresh fired on degraded path"


def test_update_postbump_snapshot_tolerates_unhealed_config(tmp_path: Path) -> None:
    """HATS-581 Fix #1: if the bump can't heal (non-strippable error), the
    post-bump re-read still raises — update must finish cleanly, no traceback."""
    result, captured = _run_degraded_update(
        tmp_path,
        version_changed=True,
        # Both pre-install and post-bump reads raise (config stays broken).
        assembler_side_effect=[
            ProjectConfigError("schema_version: int required"),
            ProjectConfigError("schema_version: int required"),
        ],
    )

    assert result.exit_code == 0, f"update crashed: {result.output}"
    assert "Traceback" not in result.output
    assert _bump_subprocess_calls(captured)


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
                   tmp_path: Path, fail_install: bool = False) -> tuple[int, str, list[tuple]]:
    """Invoke ``update`` with all heavy I/O patched; return (exit, output, subprocess calls).

    ``fail_install`` makes the legacy in-place ``pip install`` subprocess return
    a non-zero code, exercising the HATS-718 failure branch (must exit 1).
    """
    project = _setup_update_test_env(tmp_path)
    captured: list[tuple] = []

    def fake_run(cmd_args, **kwargs):
        captured.append((tuple(cmd_args), kwargs))
        if fail_install and tuple(cmd_args)[:3] == ("uv", "pip", "install"):
            return _make_completed(cmd_args, returncode=1, stderr="uv boom")
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
               return_value=["uv", "pip", "install", "ai-hats"]), \
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


def _install_called(captured: list[tuple]) -> bool:
    return any(tuple(c[0][:3]) == ("uv", "pip", "install") for c in captured)


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
    assert not _install_called(captured), \
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
    assert not _install_called(captured), \
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
    assert _install_called(captured), \
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
    assert _install_called(captured), f"pip install missing: {captured}"


def test_update_proceeds_when_probe_unknown(tmp_path: Path) -> None:
    """run_check returns None (non-git install / probe failure) → proceed."""
    exit_code, output, captured = _invoke_update(
        [], run_check_return=None, tmp_path=tmp_path,
    )
    assert exit_code == 0, f"expected exit 0, got {exit_code}; output:\n{output}"
    assert "Refusing to downgrade" not in output, \
        f"unexpected refusal on unknown probe:\n{output}"
    assert _install_called(captured), f"pip install missing: {captured}"


def test_update_skips_pip_when_installed_sha_matches_remote(tmp_path: Path) -> None:
    """installed_sha == latest_sha → pip install short-circuited, bump still runs.

    HATS-follow-up: when ``run_check`` confirms the installed SHA matches
    remote ``master``, the unconditional ``pip install --force-reinstall``
    is a 10-15s no-op (~minute on slow links) that users have mistaken
    for a hang. Reuse the probe entry to short-circuit pip; bump still
    runs so any pending in-process migrations apply.
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
    assert not _install_called(captured), \
        f"pip install ran despite SHA match: {captured}"
    assert "Already up to date" in output, \
        f"missing already-up-to-date banner:\n{output}"
    assert "skipping reinstall" in output, \
        f"missing skip-reinstall dim hint:\n{output}"
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
    assert _install_called(captured), f"pip install missing: {captured}"


def test_update_legacy_install_failure_exits_1(tmp_path: Path) -> None:
    """HATS-718: legacy in-place ``pip install`` failure → exit 1 (not 0).

    Fail-under-revert: the pre-fix bare ``return`` after the red text leaves
    click's exit code at 0, so the ``exit_code == 1`` assertion fails.
    """
    exit_code, output, captured = _invoke_update(
        [], run_check_return=_entry(ahead=0, behind=4),
        tmp_path=tmp_path, fail_install=True,
    )
    assert exit_code == 1, f"expected exit 1 on failed install, got {exit_code}; output:\n{output}"
    assert "Update failed" in output, f"missing failure text:\n{output}"


# ---------- HATS-647: managed blue-green versioned install ----------

from ai_hats.cli import maintenance as _mnt  # noqa: E402
from ai_hats.cli.maintenance import (  # noqa: E402
    _build_install_cmd,
    _flip_current,
    _is_managed_install,
    _run_managed_versioned_update,
)
from ai_hats.channel import resolve_channel  # noqa: E402
from ai_hats.models import Channel  # noqa: E402
from ai_hats.paths import (  # noqa: E402
    complete_sentinel,
    current_pointer,
    is_complete,
    read_current_sha,
    version_dir,
)


def _edge_res(url: str, sha: str):
    """HATS-764: build the edge ChannelResolution the managed-update tests
    install with — replaces the old (url=, target_sha=) call shape."""
    return resolve_channel(Channel.EDGE, repo=url, head_sha=sha)


def test_is_managed_editable_is_false(tmp_path, monkeypatch):
    """Editable dev checkout never gets versioned."""
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (True, "file:///src"))
    assert _is_managed_install(tmp_path) is False


def test_active_venv_root_uses_prefix_not_resolved_symlink(tmp_path, monkeypatch):
    """Regression guard (HATS-647): a venv's ``bin/python`` is a symlink to the
    base interpreter, so ``Path(sys.executable).resolve()`` escapes the venv.
    ``_active_venv_root`` must use ``sys.prefix`` instead — this reproduces the
    exact symlink mechanism with a real symlink, no subprocess/pip needed.

    Fail-under-revert: switching back to
    ``Path(sys.executable).resolve().parent.parent`` resolves the symlink to
    ``<base>/bin/python`` → ``<base>`` ≠ the venv root, so the assert fails.
    """
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    base_python = tmp_path / "base" / "bin" / "python3"
    base_python.parent.mkdir(parents=True)
    base_python.write_text("#!/bin/sh\n")  # stand-in base interpreter
    venv_python = venv / "bin" / "python"
    venv_python.symlink_to(base_python)  # exactly what `python -m venv` does
    monkeypatch.setattr(_mnt.sys, "prefix", str(venv))
    monkeypatch.setattr(_mnt.sys, "executable", str(venv_python))
    # Correct (sys.prefix) → the venv. Regressed (resolve executable) → base.
    assert _mnt._active_venv_root() == venv
    assert _mnt._active_venv_root() != base_python.parent.parent


def test_is_managed_default_venv(tmp_path, monkeypatch):
    """Active venv (sys.prefix) == <ai_hats_dir>/.venv → managed."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    monkeypatch.setattr(_mnt.sys, "prefix", str(venv))
    assert _is_managed_install(tmp_path) is True


def test_is_managed_versioned_venv(tmp_path, monkeypatch):
    """Active venv (sys.prefix) under versions/<sha>/ → managed."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    venv = tmp_path / ".agent" / "ai-hats" / "versions" / "deadbeef"
    monkeypatch.setattr(_mnt.sys, "prefix", str(venv))
    assert _is_managed_install(tmp_path) is True


def test_is_managed_override_venv_is_false(tmp_path, monkeypatch):
    """Active venv (sys.prefix) is a user-owned path elsewhere → not managed."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    venv = tmp_path / "user-owned"
    monkeypatch.setattr(_mnt.sys, "prefix", str(venv))
    assert _is_managed_install(tmp_path) is False


# ---- HATS-655: dormant-versioned-layout advisory ----


def _on_venv(tmp_path, monkeypatch):
    """Make sys.prefix the legacy default .venv (managed, current_run_sha None)."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    monkeypatch.setattr(
        _mnt.sys, "prefix", str(tmp_path / ".agent" / "ai-hats" / ".venv")
    )


def test_dormant_true_when_versioned_exists_but_run_from_venv(tmp_path, monkeypatch):
    _on_venv(tmp_path, monkeypatch)
    assert _mnt._versioned_layout_dormant(
        tmp_path, pre_existing_versioned=True
    ) is True


def test_dormant_false_on_first_migration(tmp_path, monkeypatch):
    """No versioned install pre-existed → running from .venv is expected."""
    _on_venv(tmp_path, monkeypatch)
    assert _mnt._versioned_layout_dormant(
        tmp_path, pre_existing_versioned=False
    ) is False


def test_dormant_false_when_running_from_versioned(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    monkeypatch.setattr(
        _mnt.sys, "prefix",
        str(tmp_path / ".agent" / "ai-hats" / "versions" / "deadbeef"),
    )
    assert _mnt._versioned_layout_dormant(
        tmp_path, pre_existing_versioned=True
    ) is False


def test_dormant_false_on_override(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    monkeypatch.setattr(_mnt.sys, "prefix", str(tmp_path / "user-owned"))
    assert _mnt._versioned_layout_dormant(
        tmp_path, pre_existing_versioned=True
    ) is False


def test_dormant_false_on_editable(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (True, "file:///src"))
    monkeypatch.setattr(
        _mnt.sys, "prefix", str(tmp_path / ".agent" / "ai-hats" / ".venv")
    )
    assert _mnt._versioned_layout_dormant(
        tmp_path, pre_existing_versioned=True
    ) is False


def test_installed_launcher_path_resolution(tmp_path, monkeypatch):
    import shutil

    # 1. AI_HATS_LAUNCHER_DEST wins.
    monkeypatch.setenv("AI_HATS_LAUNCHER_DEST", str(tmp_path / "custom" / "ai-hats"))
    assert _mnt._installed_launcher_path() == tmp_path / "custom" / "ai-hats"
    # 2. unset + on PATH → which().
    monkeypatch.delenv("AI_HATS_LAUNCHER_DEST", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: "/opt/bin/ai-hats")
    assert _mnt._installed_launcher_path() == Path("/opt/bin/ai-hats")
    # 3. unset + not on PATH → documented default.
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert _mnt._installed_launcher_path() == Path.home() / ".local" / "bin" / "ai-hats"


def test_build_install_cmd_passes_spec_through():
    """HATS-764: thin uv wrapper — the pre-shaped install_spec is passed verbatim."""
    url_cmd = _build_install_cmd("/v/bin/python", "ai-hats @ git+ssh://x/ai-hats.git@abc")
    assert url_cmd[:3] == ["uv", "pip", "install"]  # HATS-763: uv engine
    assert url_cmd[url_cmd.index("--python") + 1] == "/v/bin/python"  # B1
    assert "--reinstall" in url_cmd
    assert url_cmd[-1] == "ai-hats @ git+ssh://x/ai-hats.git@abc"
    local_cmd = _build_install_cmd("/v/bin/python", "/local/path")
    assert local_cmd[-1] == "/local/path"


def test_flip_current_atomic_write(tmp_path, monkeypatch):
    """_flip_current writes the sha to versions/current and is overwrite-safe."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    _flip_current(tmp_path, "aaaa1111")
    assert current_pointer(tmp_path).read_text().strip() == "aaaa1111"
    _flip_current(tmp_path, "bbbb2222")  # idempotent overwrite
    assert current_pointer(tmp_path).read_text().strip() == "bbbb2222"


def _versioned_fake_run(*, fail_at=None, bump_rec=None):
    """subprocess.run side-effect for _run_managed_versioned_update.

    Creates the venv dir on `uv venv`, succeeds for each phase unless
    `fail_at` ('venv'|'install'|'verify') matches; records bump python.
    """
    def fake_run(args, **kwargs):
        a = list(args)
        if "venv" in a and "uv" in a:  # uv venv --python <ver> <target>
            if fail_at == "venv":
                return _make_completed(a, returncode=1, stderr="venv boom")
            target = Path(a[-1])
            (target / "bin").mkdir(parents=True, exist_ok=True)
            # `uv venv` lays down the interpreter; uv pip install (next phase)
            # drops the ai-hats entry-point. The venv is only "usable"
            # (read_current_sha-acceptable) once both bin/python and bin/ai-hats
            # exist alongside the sentinel (HATS-657).
            (target / "bin" / "python").write_text("#!/bin/sh\n")
            (target / "bin" / "ai-hats").write_text("#!/bin/sh\n")
            return _make_completed(a, returncode=0)
        if "pip" in a:
            return _make_completed(a, returncode=1 if fail_at == "install" else 0,
                                   stderr="pip boom")
        if any("_bootstrap" in x for x in a):
            return _make_completed(a, returncode=1 if fail_at == "verify" else 0,
                                   stderr="verify boom")
        if "-c" in a:  # _version_string
            return _make_completed(a, returncode=0, stdout="9.9.9\n")
        if any("_bump_internal" in x for x in a):
            if bump_rec is not None:
                bump_rec.append(a[0])
            return _make_completed(a, returncode=0)
        return _make_completed(a, returncode=0)
    return fake_run


def test_managed_update_happy_path_flips_current(tmp_path, monkeypatch):
    """Full managed update: installs into versions/<sha>, flips current, bumps
    with the NEW venv python."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_get_changelog", lambda: "")
    bump_rec: list[str] = []
    with patch("subprocess.run", side_effect=_versioned_fake_run(bump_rec=bump_rec)):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role="assistant",
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert read_current_sha(tmp_path) == "cafef00d"
    # HATS-648: the .complete sentinel is written on a fully-successful install.
    assert is_complete(tmp_path, "cafef00d")
    # Bump ran with the NEW venv's python, not sys.executable.
    expected_python = str(version_dir(tmp_path, "cafef00d") / "bin" / "python")
    assert bump_rec == [expected_python]


def test_managed_update_venv_create_failure_exits_1(tmp_path, monkeypatch):
    """HATS-718: ``python -m venv`` failure → exit 1 (not a bare return/exit 0),
    and current is NOT flipped."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    with patch("subprocess.run", side_effect=_versioned_fake_run(fail_at="venv")):
        with pytest.raises(SystemExit) as exc:
            _run_managed_versioned_update(
                tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
                old_version="1.0.0", active_role=None,
                config_unreadable=False, migrate_force=False, check_branches=False,
            )
    assert exc.value.code == 1, f"expected exit 1, got {exc.value.code!r}"
    assert read_current_sha(tmp_path) is None  # never flipped


def test_managed_update_install_failure_does_not_flip(tmp_path, monkeypatch):
    """pip install fails → exit 1 (HATS-718), current is NOT flipped (tool stays
    on old sha) and no bump runs."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    bump_rec: list[str] = []
    with patch("subprocess.run",
               side_effect=_versioned_fake_run(fail_at="install", bump_rec=bump_rec)):
        with pytest.raises(SystemExit) as exc:
            _run_managed_versioned_update(
                tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
                old_version="1.0.0", active_role="assistant",
                config_unreadable=False, migrate_force=False, check_branches=False,
            )
    assert exc.value.code == 1, f"expected exit 1, got {exc.value.code!r}"
    assert read_current_sha(tmp_path) is None  # never flipped
    assert not current_pointer(tmp_path).exists()
    assert bump_rec == []  # bump skipped on failed install


def test_managed_update_verify_failure_does_not_flip(tmp_path, monkeypatch):
    """Post-install verify fails → exit 1 (HATS-718), current is NOT flipped, and
    the residual dir carries NO .complete sentinel (HATS-648) so the recovery
    sweep reclaims it and read_current_sha never trusts it."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    with patch("subprocess.run", side_effect=_versioned_fake_run(fail_at="verify")):
        with pytest.raises(SystemExit) as exc:
            _run_managed_versioned_update(
                tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
                old_version="1.0.0", active_role=None,
                config_unreadable=False, migrate_force=False, check_branches=False,
            )
    assert exc.value.code == 1, f"expected exit 1, got {exc.value.code!r}"
    assert read_current_sha(tmp_path) is None
    # The half-written dir exists but is incomplete — no sentinel.
    assert version_dir(tmp_path, "cafef00d").is_dir()
    assert not is_complete(tmp_path, "cafef00d")


def test_managed_update_already_current_skips_install(tmp_path, monkeypatch):
    """current already == target + dir present → no venv/pip/verify, bump only."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    # Pre-seed: a USABLE versions/cafef00d/ venv (bin/python + bin/ai-hats +
    # .complete sentinel) and current → it. HATS-648: completeness requires the
    # sentinel; HATS-657: read_current_sha additionally requires bin/python.
    vbin = version_dir(tmp_path, "cafef00d") / "bin"
    vbin.mkdir(parents=True, exist_ok=True)
    (vbin / "python").write_text("#!/bin/sh\n")
    (vbin / "ai-hats").write_text("#!/bin/sh\n")
    complete_sentinel(tmp_path, "cafef00d").write_text("", encoding="utf-8")
    _flip_current(tmp_path, "cafef00d")
    calls: list[list[str]] = []

    def rec_run(args, **kwargs):
        calls.append(list(args))
        return _make_completed(list(args), returncode=0)

    with patch("subprocess.run", side_effect=rec_run):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role="assistant",
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    # No venv create / pip install / verify happened.
    assert not any("venv" in c and "uv" in c for c in calls)
    assert not any("pip" in c for c in calls)
    # Bump ran with sys.executable (the live venv already IS the target).
    assert any(
        any("_bump_internal" in x for x in c) and c[0] == sys.executable
        for c in calls
    )


def test_managed_update_rebuilds_broken_python_versioned(tmp_path, monkeypatch):
    """HATS-657 (both consequences): current → a COMPLETE versioned venv whose
    bin/python is gone (a host python upgrade dangled the interpreter), and this
    update runs from the healed legacy .venv.

    #1 The broken dir must be REBUILT, not reused: read_current_sha returns None
       (not usable) → already_current False, and the reuse gate also requires
       bin/python → the dir falls through to the rmtree+rebuild branch instead of
       being reused with a dead interpreter (which would crash _version_string).
    #2 The HATS-655 dormancy advisory must NOT false-fire: pre_existing_versioned
       is False (the pre-existing versioned was not usable), so the heal is silent
       — the launcher correctly skipped a BROKEN venv, it is not stale."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_get_changelog", lambda: "")
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    # This run came from the (healed) legacy .venv — the post-HATS-656 reality.
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".agent" / "ai-hats" / ".venv"))
    # Pre-seed a COMPLETE versioned venv (sentinel + bin/ai-hats) but NO bin/python
    # — the host-python-upgrade symptom — and point current at it.
    vbin = version_dir(tmp_path, "cafef00d") / "bin"
    vbin.mkdir(parents=True, exist_ok=True)
    (vbin / "ai-hats").write_text("#!/bin/sh\n")
    complete_sentinel(tmp_path, "cafef00d").write_text("", encoding="utf-8")
    _flip_current(tmp_path, "cafef00d")
    assert read_current_sha(tmp_path) is None  # broken venv is not usable
    printed = _capture_prints(monkeypatch)

    # Record every subprocess call while delegating to the real fake (which
    # rebuilds bin/python + bin/ai-hats on `uv venv`).
    fake = _versioned_fake_run()
    calls: list[list[str]] = []

    def rec_run(args, **kwargs):
        calls.append(list(args))
        return fake(args, **kwargs)

    with patch("subprocess.run", side_effect=rec_run):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role=None,
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    # #1 REBUILT (not reused): a venv create + pip install ran for the target sha.
    assert any("venv" in c and "uv" in c for c in calls)
    assert any("pip" in c for c in calls)
    # The interpreter is restored and the sha is usable / current again.
    assert (version_dir(tmp_path, "cafef00d") / "bin" / "python").exists()
    assert read_current_sha(tmp_path) == "cafef00d"
    # #2 No false dormancy advisory — the broken versioned was correctly skipped.
    assert not any("host launcher is not using" in p for p in printed)


def test_managed_update_sweeps_incomplete_residue_before_build(tmp_path, monkeypatch):
    """HATS-648: a `self update` reclaims old incomplete residue from a prior
    crashed update (eager sweep at self-update start) while installing the new
    sha."""
    import os
    import time

    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setattr(_mnt, "_get_changelog", lambda: "")
    # Plant aged incomplete residue (no .complete sentinel) from a past crash.
    residue = version_dir(tmp_path, "0ld0bad0")
    (residue / "bin").mkdir(parents=True, exist_ok=True)
    (residue / "bin" / "ai-hats").write_text("#!/bin/sh\n", encoding="utf-8")
    old = time.time() - 48 * 3600
    os.utime(residue, (old, old))

    with patch("subprocess.run", side_effect=_versioned_fake_run()):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role=None,
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert not residue.exists()  # crash residue reclaimed
    assert read_current_sha(tmp_path) == "cafef00d"  # new install is current


def test_managed_update_reuses_complete_dir_without_reinstall(tmp_path, monkeypatch):
    """HATS-648 safety guard: a COMPLETE versions/<sha>/ that is NOT current is
    reused (current re-flips to it) WITHOUT rmtree+reinstall — a blind reinstall
    would destroy a dir that may be a live pinned run's frozen env."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_get_changelog", lambda: "")
    # Pre-seed a complete versions/cafef00d/ (sentinel) but point current ELSEWHERE.
    vbin = version_dir(tmp_path, "cafef00d") / "bin"
    vbin.mkdir(parents=True, exist_ok=True)
    (vbin / "ai-hats").write_text("#!/bin/sh\n")
    (vbin / "python").write_text("#!/bin/sh\n")
    complete_sentinel(tmp_path, "cafef00d").write_text("", encoding="utf-8")
    _flip_current(tmp_path, "0ldc0de0")  # current points at a different sha
    calls: list[list[str]] = []

    def rec_run(args, **kwargs):
        calls.append(list(args))
        return _make_completed(list(args), returncode=0, stdout="9.9.9\n")

    with patch("subprocess.run", side_effect=rec_run):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role=None,
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    # Reused: no rebuild, but current re-flipped to the complete dir.
    assert not any("venv" in c and "uv" in c for c in calls)
    assert not any("pip" in c for c in calls)
    assert read_current_sha(tmp_path) == "cafef00d"
    assert is_complete(tmp_path, "cafef00d")


def test_managed_update_reclaims_legacy_venv_when_on_versioned(tmp_path, monkeypatch):
    """HATS-653: an updater already running from a versioned venv (current_run_sha
    not None) reclaims the orphaned legacy .venv during the update."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setattr(_mnt, "_get_changelog", lambda: "")
    # This updater runs FROM versions/0ldc0de0 → current_run_sha resolves.
    monkeypatch.setattr(sys, "prefix", str(version_dir(tmp_path, "0ldc0de0")))
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    (legacy / "bin").mkdir(parents=True, exist_ok=True)

    with patch("subprocess.run", side_effect=_versioned_fake_run()):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role=None,
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert not legacy.exists()  # legacy .venv reclaimed
    assert read_current_sha(tmp_path) == "cafef00d"


def test_managed_update_keeps_legacy_venv_when_on_venv(tmp_path, monkeypatch):
    """HATS-653: the first migration update runs FROM .venv (current_run_sha None)
    → the legacy .venv is kept (never deleted out from under the live process)."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setattr(_mnt, "_get_changelog", lambda: "")
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    (legacy / "bin").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))  # running from .venv itself

    with patch("subprocess.run", side_effect=_versioned_fake_run()):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role=None,
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert legacy.exists()  # kept — we ran from it
    assert read_current_sha(tmp_path) == "cafef00d"


def _capture_prints(monkeypatch):
    printed: list[str] = []
    monkeypatch.setattr(
        _mnt.console, "print",
        lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    return printed


def test_managed_update_warns_when_launcher_dormant(tmp_path, monkeypatch):
    """HATS-655: a versioned install pre-existed AND this update ran from the
    legacy .venv → the dormant-layout hint fires."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_get_changelog", lambda: "")
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    monkeypatch.setattr(
        sys, "prefix", str(tmp_path / ".agent" / "ai-hats" / ".venv")
    )
    # A USABLE versioned install already exists → pre_existing_versioned True.
    # HATS-657: must carry bin/python too, else read_current_sha treats it as
    # unusable and pre_existing_versioned would be False — that is the python-
    # broken case (correctly silent), NOT the genuine stale-launcher dormancy
    # this test exercises.
    vbin = version_dir(tmp_path, "0ldc0de0") / "bin"
    vbin.mkdir(parents=True, exist_ok=True)
    (vbin / "ai-hats").write_text("#!/bin/sh\n")
    (vbin / "python").write_text("#!/bin/sh\n")
    complete_sentinel(tmp_path, "0ldc0de0").write_text("", encoding="utf-8")
    _flip_current(tmp_path, "0ldc0de0")
    printed = _capture_prints(monkeypatch)

    with patch("subprocess.run", side_effect=_versioned_fake_run()):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role=None,
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert any(
        "host launcher is not using the versioned install" in p for p in printed
    )
    assert any("install-launcher.sh" in p for p in printed)


def test_managed_update_no_dormant_hint_on_first_migration(tmp_path, monkeypatch):
    """No versioned install pre-existed → running from .venv is expected, no hint."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setattr(_mnt, "_get_changelog", lambda: "")
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (False, None))
    monkeypatch.setattr(
        sys, "prefix", str(tmp_path / ".agent" / "ai-hats" / ".venv")
    )
    printed = _capture_prints(monkeypatch)

    with patch("subprocess.run", side_effect=_versioned_fake_run()):
        _run_managed_versioned_update(
            tmp_path, _edge_res("git+ssh://x/ai-hats.git", "cafef00d"),
            old_version="1.0.0", active_role=None,
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert read_current_sha(tmp_path) == "cafef00d"  # update succeeded
    assert not any("host launcher is not using" in p for p in printed)


# ---------- HATS-764: channel routing + per-channel guard ----------

from ai_hats.cli.maintenance import (  # noqa: E402
    _build_managed_resolution,
    _classify_semver_downgrade,
)


def _setup_channel_env(tmp_path: Path, channel: str, *, extra: str = "") -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: assistant\n"
        f"harness:\n  channel: {channel}\n{extra}"
    )
    return project


def test_semver_downgrade_classify():
    # refuse lower, allow equal/higher, dev-vs-release boundary, unparseable→allow.
    assert _classify_semver_downgrade("0.9.0", "0.8.1") is True
    assert _classify_semver_downgrade("0.8.1", "0.8.1") is False
    assert _classify_semver_downgrade("0.8.0", "0.8.1") is False
    # installed dev precedes the release → upgrading to the release is allowed.
    assert _classify_semver_downgrade("0.8.1.dev105+g589f167", "0.8.1") is False
    # release installed, an older dev target → downgrade.
    assert _classify_semver_downgrade("0.8.1", "0.8.1.dev3") is True
    # unparseable installed (editable 'unknown') → allow.
    assert _classify_semver_downgrade("unknown", "0.8.1") is False


def test_build_managed_resolution_stable():
    r = _build_managed_resolution(
        Channel.STABLE, revision_repo=None, revision_sha=None,
        harness_repo=None, latest_stable="0.8.1",
    )
    assert r.version_id == "0.8.1"
    assert r.install_spec == "ai-hats==0.8.1"
    assert r.mutable is False


def test_build_managed_resolution_revision_pin_wins_over_channel():
    r = _build_managed_resolution(
        Channel.STABLE, revision_repo="git+ssh://x/y.git", revision_sha="abc123",
        harness_repo=None, latest_stable="0.8.1",
    )
    assert r.version_id == "abc123"
    assert r.install_spec == "ai-hats @ git+ssh://x/y.git@abc123"


def test_build_managed_resolution_edge_fetches_repo_head(monkeypatch):
    monkeypatch.delenv("AI_HATS_REPO_URL", raising=False)
    # version_id comes from the edge repo's actual HEAD (ls-remote), not the
    # upstream-master probe (which is hardwired to a possibly-different repo).
    monkeypatch.setattr("ai_hats.channel.fetch_edge_head_sha", lambda repo: "feed1234")
    r = _build_managed_resolution(
        Channel.EDGE, revision_repo=None, revision_sha=None,
        harness_repo=None, latest_stable=None,
    )
    assert r.version_id == "feed1234"
    assert r.install_spec == (
        "ai-hats @ git+https://github.com/muratovv/ai-hats.git@feed1234"
    )


def test_build_managed_resolution_edge_offline_exits_2(monkeypatch):
    monkeypatch.delenv("AI_HATS_REPO_URL", raising=False)
    monkeypatch.setattr("ai_hats.channel.fetch_edge_head_sha", lambda repo: None)
    with pytest.raises(SystemExit) as exc:
        _build_managed_resolution(
            Channel.EDGE, revision_repo=None, revision_sha=None,
            harness_repo=None, latest_stable=None,
        )
    assert exc.value.code == 2


def test_update_stable_refuses_semver_downgrade(tmp_path, monkeypatch):
    """channel: stable + a lower published tag than installed → exit 3."""
    project = _setup_channel_env(tmp_path, "stable")
    monkeypatch.setattr("ai_hats.channel.fetch_latest_stable_version", lambda: "0.0.1")
    with patch("ai_hats.cli.maintenance._project_dir", return_value=project), \
         patch("ai_hats.__version__", "0.9.0"):
        result = CliRunner().invoke(update, [])
    assert result.exit_code == DOWNGRADE_REFUSAL_EXIT_CODE == 3, result.output
    assert "newer than" in result.output and "0.0.1" in result.output


def test_update_stable_fetch_unreachable_exits_2(tmp_path, monkeypatch):
    """channel: stable + PyPI unreachable → fail loud, exit 2, no silent fallback."""
    from ai_hats.channel import ChannelResolveError

    project = _setup_channel_env(tmp_path, "stable")

    def boom():
        raise ChannelResolveError("could not resolve latest stable version from PyPI")

    monkeypatch.setattr("ai_hats.channel.fetch_latest_stable_version", boom)
    with patch("ai_hats.cli.maintenance._project_dir", return_value=project):
        result = CliRunner().invoke(update, [])
    assert result.exit_code == 2, result.output
    assert "could not resolve latest stable version from PyPI" in result.output


def test_update_local_editable_in_place(tmp_path, monkeypatch):
    """channel: local → `uv pip install -e <path>` in place, no versioned dir."""
    project = _setup_channel_env(tmp_path, "local", extra="  path: .\n")
    captured: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured.append(list(args))
        return _make_completed(list(args), returncode=0)

    monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/uv")  # _require_uv passes
    with patch("ai_hats.cli.maintenance._project_dir", return_value=project), \
         patch("subprocess.run", side_effect=fake_run):
        result = CliRunner().invoke(update, [])
    assert result.exit_code == 0, result.output
    editable = [c for c in captured if c[:3] == ["uv", "pip", "install"] and "-e" in c]
    assert len(editable) == 1, f"expected one editable install, got {captured}"
    assert editable[0][-1] == "."
    # No versioned dir is created for a local editable install.
    assert not (project / ".agent" / "ai-hats" / "versions").exists()
