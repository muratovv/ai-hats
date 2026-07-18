"""HATS-1013: unit tests for ``ai_hats.env_drift`` — the uv-wrapping detector.

The runner/which seams are injected (self_heal.py precedent); no real uv here.
"""

import subprocess
from pathlib import Path
from subprocess import CompletedProcess

from ai_hats.env_drift import ENV_DRIFT_FIX, stale_dev_env_warnings

# Captured from the real S0 probe (uv 0.10.11, 2026-07-16) — task card.
PROBE_OUTPUT = """\
Would use project environment at: .venv
Resolved 83 packages in 17ms
Would update lockfile at: uv.lock
Would download 1 package
Would uninstall 3 packages
Would install 3 packages
 - ai-hats==0.13.3.dev28+ga783ae5bc (from file:///Users/fedor/dev/ai-hats)
 + ai-hats @ file:///Users/fedor/dev/ai-hats
 - ai-hats-library==0.1.0 (from file:///Users/fedor/dev/ai-hats/packages/ai-hats-library)
 + ai-hats-library==0.2.0 (from file:///Users/fedor/dev/ai-hats/packages/ai-hats-library)
 - ai-hats-tracker==0.5.0 (from file:///Users/fedor/dev/ai-hats/packages/ai-hats-tracker)
 + ai-hats-tracker==0.6.0 (from file:///Users/fedor/dev/ai-hats/packages/ai-hats-tracker)
The environment is outdated; run `uv sync` to update the environment
"""


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """A fake editable checkout: repo root + its .venv prefix."""
    venv = tmp_path / ".venv"
    venv.mkdir()
    return tmp_path, venv


def _runner_returning(code: int, output: str = ""):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return CompletedProcess(cmd, code, stdout=output, stderr="")

    run.calls = calls
    return run


def test_drifted_env_yields_one_warning_naming_members(tmp_path):
    repo, venv = _repo(tmp_path)
    runner = _runner_returning(1, PROBE_OUTPUT)

    warnings = stale_dev_env_warnings(
        repo_root=repo, venv_prefix=venv, runner=runner, which=lambda _: "/usr/bin/uv"
    )

    assert len(warnings) == 1
    text = warnings[0]
    assert "ai-hats-library 0.1.0 -> 0.2.0" in text
    assert "ai-hats-tracker 0.5.0 -> 0.6.0" in text
    assert "ai-hats" in text  # root: no target version, name only
    assert ENV_DRIFT_FIX in text
    # fix command on its own line, unquoted, so it copy-pastes clean
    assert f"\n    {ENV_DRIFT_FIX}" in text
    assert f"'{ENV_DRIFT_FIX}'" not in text
    # verdict comes from `uv sync --check --inexact --all-packages`, pinned to the repo
    (cmd,) = runner.calls
    assert cmd[:2] == ["uv", "sync"]
    for flag in ("--check", "--inexact", "--all-packages"):
        assert flag in cmd
    assert str(repo) in cmd


def test_in_sync_env_yields_nothing(tmp_path):
    repo, venv = _repo(tmp_path)

    assert (
        stale_dev_env_warnings(
            repo_root=repo,
            venv_prefix=venv,
            runner=_runner_returning(0),
            which=lambda _: "/usr/bin/uv",
        )
        == []
    )


def test_uv_error_exit_fails_open(tmp_path):
    repo, venv = _repo(tmp_path)

    assert (
        stale_dev_env_warnings(
            repo_root=repo,
            venv_prefix=venv,
            runner=_runner_returning(2, "error: unexpected argument"),
            which=lambda _: "/usr/bin/uv",
        )
        == []
    )


def test_runner_exception_fails_open(tmp_path):
    repo, venv = _repo(tmp_path)

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 15)

    assert (
        stale_dev_env_warnings(
            repo_root=repo, venv_prefix=venv, runner=boom, which=lambda _: "/usr/bin/uv"
        )
        == []
    )


def test_uv_missing_skips_without_running(tmp_path):
    repo, venv = _repo(tmp_path)
    runner = _runner_returning(1, PROBE_OUTPUT)

    assert (
        stale_dev_env_warnings(
            repo_root=repo, venv_prefix=venv, runner=runner, which=lambda _: None
        )
        == []
    )
    assert runner.calls == []


def test_foreign_venv_prefix_skips_without_running(tmp_path):
    repo, _ = _repo(tmp_path)
    foreign = tmp_path / "elsewhere" / ".venv"
    foreign.mkdir(parents=True)
    runner = _runner_returning(1, PROBE_OUTPUT)

    assert (
        stale_dev_env_warnings(
            repo_root=repo, venv_prefix=foreign, runner=runner, which=lambda _: "/usr/bin/uv"
        )
        == []
    )
    assert runner.calls == []


def test_symlinked_venv_prefix_still_matches(tmp_path):
    # HATS-1013 live-check regression: uv/venv interpreters are symlinks out of
    # .venv, so identity must compare resolved venv prefixes, not executables.
    repo, venv = _repo(tmp_path)
    link = tmp_path / "link-to-venv"
    link.symlink_to(venv)

    warnings = stale_dev_env_warnings(
        repo_root=repo,
        venv_prefix=link,
        runner=_runner_returning(1, PROBE_OUTPUT),
        which=lambda _: "/usr/bin/uv",
    )

    assert len(warnings) == 1


def test_no_editable_root_skips(monkeypatch, tmp_path):
    import ai_hats.paths

    monkeypatch.setattr(ai_hats.paths, "editable_install_root", lambda dist: None)

    assert stale_dev_env_warnings(runner=_runner_returning(1, PROBE_OUTPUT)) == []


def test_unparseable_output_still_warns_with_hint(tmp_path):
    repo, venv = _repo(tmp_path)

    warnings = stale_dev_env_warnings(
        repo_root=repo,
        venv_prefix=venv,
        runner=_runner_returning(1, "The environment is outdated"),
        which=lambda _: "/usr/bin/uv",
    )

    assert warnings == [f"dev env outdated — run:\n    {ENV_DRIFT_FIX}"]
