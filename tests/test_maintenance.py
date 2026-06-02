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
               return_value=["pip", "install", "ai-hats"]), \
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
               return_value=["pip", "install", "ai-hats"]), \
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


# ---------- HATS-647: managed blue-green versioned install ----------

from ai_hats.cli import maintenance as _mnt  # noqa: E402
from ai_hats.cli.maintenance import (  # noqa: E402
    _build_install_cmd,
    _flip_current,
    _is_managed_install,
    _run_managed_versioned_update,
)
from ai_hats.paths import current_pointer, read_current_sha, version_dir  # noqa: E402


def test_is_managed_editable_is_false(tmp_path, monkeypatch):
    """Editable dev checkout never gets versioned."""
    monkeypatch.setattr(_mnt, "_is_editable_install", lambda: (True, "file:///src"))
    assert _is_managed_install(tmp_path) is False


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


def test_build_install_cmd_url_and_local():
    """Install target: PEP 508 `name @ url@ref` for URLs, bare path@ref otherwise."""
    url_cmd = _build_install_cmd("/v/bin/python", "git+ssh://x/ai-hats.git", "abc")
    assert url_cmd[:5] == ["/v/bin/python", "-m", "pip", "install", "--force-reinstall"]
    assert url_cmd[-1] == "ai-hats @ git+ssh://x/ai-hats.git@abc"
    # Local path: bare path (pip can't take @ref on a local path).
    local_cmd = _build_install_cmd("/v/bin/python", "/local/path", "abc")
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

    Creates the venv dir on `-m venv`, succeeds for each phase unless
    `fail_at` ('venv'|'install'|'verify') matches; records bump python.
    """
    def fake_run(args, **kwargs):
        a = list(args)
        if "venv" in a and "-m" in a:  # python -m venv <target>
            if fail_at == "venv":
                return _make_completed(a, returncode=1, stderr="venv boom")
            target = Path(a[-1])
            (target / "bin").mkdir(parents=True, exist_ok=True)
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
            tmp_path, url="git+ssh://x/ai-hats.git", target_sha="cafef00d",
            old_version="1.0.0", active_role="assistant",
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert read_current_sha(tmp_path) == "cafef00d"
    # Bump ran with the NEW venv's python, not sys.executable.
    expected_python = str(version_dir(tmp_path, "cafef00d") / "bin" / "python")
    assert bump_rec == [expected_python]


def test_managed_update_install_failure_does_not_flip(tmp_path, monkeypatch):
    """pip install fails → current is NOT flipped (tool stays on old sha) and
    no bump runs."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    bump_rec: list[str] = []
    with patch("subprocess.run",
               side_effect=_versioned_fake_run(fail_at="install", bump_rec=bump_rec)):
        _run_managed_versioned_update(
            tmp_path, url="git+ssh://x/ai-hats.git", target_sha="cafef00d",
            old_version="1.0.0", active_role="assistant",
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert read_current_sha(tmp_path) is None  # never flipped
    assert not current_pointer(tmp_path).exists()
    assert bump_rec == []  # bump skipped on failed install


def test_managed_update_verify_failure_does_not_flip(tmp_path, monkeypatch):
    """Post-install verify fails → current is NOT flipped."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    with patch("subprocess.run", side_effect=_versioned_fake_run(fail_at="verify")):
        _run_managed_versioned_update(
            tmp_path, url="git+ssh://x/ai-hats.git", target_sha="cafef00d",
            old_version="1.0.0", active_role=None,
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    assert read_current_sha(tmp_path) is None


def test_managed_update_already_current_skips_install(tmp_path, monkeypatch):
    """current already == target + dir present → no venv/pip/verify, bump only."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    # Pre-seed: versions/cafef00d/ exists and current points at it.
    (version_dir(tmp_path, "cafef00d")).mkdir(parents=True, exist_ok=True)
    _flip_current(tmp_path, "cafef00d")
    calls: list[list[str]] = []

    def rec_run(args, **kwargs):
        calls.append(list(args))
        return _make_completed(list(args), returncode=0)

    with patch("subprocess.run", side_effect=rec_run):
        _run_managed_versioned_update(
            tmp_path, url="git+ssh://x/ai-hats.git", target_sha="cafef00d",
            old_version="1.0.0", active_role="assistant",
            config_unreadable=False, migrate_force=False, check_branches=False,
        )
    # No venv create / pip install / verify happened.
    assert not any("venv" in c and "-m" in c for c in calls)
    assert not any("pip" in c for c in calls)
    # Bump ran with sys.executable (the live venv already IS the target).
    assert any(
        any("_bump_internal" in x for x in c) and c[0] == sys.executable
        for c in calls
    )
