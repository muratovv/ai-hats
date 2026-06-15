"""Tests for scripts/bootstrap.sh (HATS-336).

Strategy: build a tmp `scripts/` dir with a real copy of bootstrap.sh +
stubs for install-launcher.sh and ai-hats-launcher. Run bootstrap.sh as
subprocess; the stub launcher records every invocation (args +
AI_HATS_REPO_URL env) to a log file we then assert against.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests._cli_helpers import assert_command_exists


pytestmark = pytest.mark.integration


REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup_fake_scripts(scripts_dir: Path, launcher_calls_log: Path) -> Path:
    """Build a fake scripts/ dir with bootstrap.sh + stubbed deps.

    Returns the bootstrap.sh path inside the fake scripts dir.
    """
    scripts_dir.mkdir(parents=True, exist_ok=True)

    # Real bootstrap.sh, copied so SCRIPT_DIR resolves into our fake dir.
    bootstrap_copy = scripts_dir / "bootstrap.sh"
    shutil.copy(BOOTSTRAP, bootstrap_copy)

    # Stub launcher: appends `REPO=...|ARGS=...` to log on every call.
    stub_launcher = scripts_dir / "ai-hats-launcher"
    stub_launcher.write_text(
        '#!/usr/bin/env bash\n'
        f'echo "REPO=${{AI_HATS_REPO_URL:-}}|ARGS=$*" >> "{launcher_calls_log}"\n'
        'exit 0\n'
    )
    _make_executable(stub_launcher)

    # Stub install-launcher.sh: copies the stub launcher to AI_HATS_LAUNCHER_DEST.
    installer = scripts_dir / "install-launcher.sh"
    installer.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'mkdir -p "$(dirname "$AI_HATS_LAUNCHER_DEST")"\n'
        f'cp "{stub_launcher}" "$AI_HATS_LAUNCHER_DEST"\n'
        'chmod +x "$AI_HATS_LAUNCHER_DEST"\n'
        'exit 0\n'
    )
    _make_executable(installer)

    return bootstrap_copy


def _run_bootstrap(bootstrap, *args, cwd, launcher_dest, env_extra=None):
    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(bootstrap), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def _setup_env(tmp_path):
    """Return (scripts_dir, project_dir, launcher_dest, log, bootstrap)."""
    scripts_dir = tmp_path / "scripts"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    launcher_dest = tmp_path / "fake-bin" / "ai-hats"
    launcher_dest.parent.mkdir(parents=True)
    log = tmp_path / "calls.log"
    bootstrap = _setup_fake_scripts(scripts_dir, log)
    return scripts_dir, project_dir, launcher_dest, log, bootstrap


def test_bootstrap_installs_launcher_then_invokes_self_update(tmp_path):
    """install-launcher writes the launcher; bootstrap then calls `self update`."""
    assert_command_exists("self", "update")
    _, project, dest, log, bootstrap = _setup_env(tmp_path)

    res = _run_bootstrap(bootstrap, cwd=project, launcher_dest=dest)

    assert res.returncode == 0, f"stderr={res.stderr}\nstdout={res.stdout}"
    assert dest.is_file()
    assert os.access(dest, os.X_OK)
    log_text = log.read_text()
    assert "ARGS=self update" in log_text


def test_bootstrap_propagates_role_provider_to_init(tmp_path):
    """-r foo -p bar → launcher called with `init --role foo --provider bar`."""
    assert_command_exists("self", "init")
    assert_command_exists("self", "update")
    _, project, dest, log, bootstrap = _setup_env(tmp_path)

    res = _run_bootstrap(
        bootstrap, "-r", "go-dev", "-p", "claude",
        cwd=project, launcher_dest=dest,
    )

    assert res.returncode == 0, res.stderr
    log_text = log.read_text()
    assert "ARGS=self update" in log_text
    assert "ARGS=self init --role go-dev --provider claude" in log_text


def test_bootstrap_local_repo_path_becomes_repo_url(tmp_path):
    """--local <path> → self update env carries AI_HATS_REPO_URL=<path>."""
    _, project, dest, log, bootstrap = _setup_env(tmp_path)
    local_repo = tmp_path / "myrepo"
    local_repo.mkdir()
    (local_repo / "pyproject.toml").write_text("[project]\nname='dummy'\n")

    res = _run_bootstrap(
        bootstrap, "--local", str(local_repo),
        cwd=project, launcher_dest=dest,
    )

    assert res.returncode == 0, res.stderr
    self_update_lines = [
        line for line in log.read_text().splitlines()
        if "ARGS=self update" in line
    ]
    assert len(self_update_lines) == 1
    assert f"REPO={local_repo}" in self_update_lines[0]


def test_bootstrap_custom_repo_url_propagated(tmp_path):
    """--repo <url> → self update env carries AI_HATS_REPO_URL=<url>."""
    _, project, dest, log, bootstrap = _setup_env(tmp_path)

    res = _run_bootstrap(
        bootstrap, "--repo", "git+ssh://custom.example/repo.git",
        cwd=project, launcher_dest=dest,
    )

    assert res.returncode == 0, res.stderr
    self_update_lines = [
        line for line in log.read_text().splitlines()
        if "ARGS=self update" in line
    ]
    assert len(self_update_lines) == 1
    assert "REPO=git+ssh://custom.example/repo.git" in self_update_lines[0]


def test_bootstrap_without_role_skips_init_and_prints_hint(tmp_path):
    """No -r/-p → bootstrap skips init call and prints next-step hint."""
    _, project, dest, log, bootstrap = _setup_env(tmp_path)

    res = _run_bootstrap(bootstrap, cwd=project, launcher_dest=dest)

    assert res.returncode == 0, res.stderr
    assert "ai-hats self init -r" in res.stdout
    log_text = log.read_text()
    # Only self update was invoked; no init.
    assert log_text.count("ARGS=") == 1
    assert "ARGS=self update" in log_text


def test_bootstrap_default_repo_url_blank_when_no_override(tmp_path):
    """No --repo / --local / parent-pyproject → launcher receives empty REPO env
    (launcher falls back to its built-in default)."""
    _, project, dest, log, bootstrap = _setup_env(tmp_path)

    res = _run_bootstrap(bootstrap, cwd=project, launcher_dest=dest)

    assert res.returncode == 0, res.stderr
    self_update_lines = [
        line for line in log.read_text().splitlines()
        if "ARGS=self update" in line
    ]
    assert len(self_update_lines) == 1
    assert "REPO=|" in self_update_lines[0]  # empty REPO value


def test_bootstrap_auto_installs_uv_when_absent(tmp_path):
    """HATS-763: uv missing → bootstrap runs the astral installer (stubbed) and
    refreshes PATH in-process so the very next `command -v uv` finds it, then
    proceeds to `self update`. Keeps the one-command install honest on a host
    with neither uv nor Python."""
    scripts_dir, project, dest, log, bootstrap = _setup_env(tmp_path)
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)

    # Stub `curl`: simulates the astral installer by dropping a uv stub into
    # ~/.local/bin (side effect) and emitting a no-op to the `| sh` consumer.
    stubbin = tmp_path / "stubbin"
    stubbin.mkdir()
    curl = stubbin / "curl"
    curl.write_text(
        '#!/usr/bin/env bash\n'
        'mkdir -p "$HOME/.local/bin"\n'
        'printf \'#!/usr/bin/env bash\\necho "uv 0.0.0-stub"\\nexit 0\\n\' '
        '> "$HOME/.local/bin/uv"\n'
        'chmod +x "$HOME/.local/bin/uv"\n'
        'echo "true"\n'  # piped into `sh` — harmless no-op
    )
    _make_executable(curl)

    # PATH excludes the real uv (~/.local/bin / ~/.cargo/bin) so the auto-install
    # branch fires; keeps system bins + our stub curl.
    env_extra = {
        "HOME": str(home),
        "PATH": f"{stubbin}:/usr/bin:/bin:/usr/sbin:/sbin",
    }
    res = _run_bootstrap(bootstrap, cwd=project, launcher_dest=dest, env_extra=env_extra)

    assert res.returncode == 0, f"stderr={res.stderr}\nstdout={res.stdout}"
    assert (home / ".local" / "bin" / "uv").is_file(), "astral installer stub did not create uv"
    assert "installing via astral.sh" in res.stdout
    assert "ARGS=self update" in log.read_text(), "self update must run after PATH refresh"
