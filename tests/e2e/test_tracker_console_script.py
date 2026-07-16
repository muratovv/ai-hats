"""E2E: ``pip install ai-hats-tracker`` puts ``ai-hats-tracker`` on PATH (HATS-991).

ADR-0016 makes the `backlog-manager` skill declare `requires.cli: ai-hats-tracker`
and probe it with `ai-hats-tracker --version`. For that probe to be satisfiable,
the engine package must expose a `[project.scripts]` console entry. This builds
the real `ai-hats-tracker` wheel (plus its `ai-hats-core` dep) and installs it
into a bare venv, then runs the EXACT `requires.cli.check` probe — the bare
`ai-hats-tracker --version` command resolved on `PATH` — and asserts exit 0.

Fail-under-revert (per `dev_rule_e2e_gate`): drop the `[project.scripts]` table
from `packages/ai-hats-tracker/pyproject.toml` → the wheel install materialises
no `<venv>/bin/ai-hats-tracker` → the console file is absent and the bare probe
is "command not found" → both assertions fail. Real `uv build` + `uv` install.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.env import clean_env
from _helpers.venv import network_available, venv_unavailable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORE_DIR = REPO_ROOT / "packages" / "ai-hats-core"
TRACKER_DIR = REPO_ROOT / "packages" / "ai-hats-tracker"

pytestmark = pytest.mark.install_heavy  # real wheel build + install → capped via conftest


def _run(cmd, *, cwd, env, timeout):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{cmd} exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_tracker_console_script_resolves_on_path(tmp_path):
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build/install the tracker wheel")

    env = clean_env()
    wheeldir = tmp_path / "wheels"
    # Build the tracker + its only workspace dep (ai-hats-core) so the install
    # resolves ai-hats-core from --find-links, third-party deps from the cache.
    for pkg in (CORE_DIR, TRACKER_DIR):
        _run(["uv", "build", "--wheel", "--out-dir", str(wheeldir), str(pkg)],
             cwd=tmp_path, env=env, timeout=180)
    assert sorted(wheeldir.glob("ai_hats_tracker-*.whl")), "no ai-hats-tracker wheel built"

    venv = tmp_path / "venv"
    _run(["uv", "venv", "--python", "3.11", str(venv)], cwd=tmp_path, env=env, timeout=120)
    _run(["uv", "pip", "install", "--python", str(venv / "bin" / "python"),
          "--find-links", str(wheeldir), "ai-hats-tracker"],
         cwd=tmp_path, env=env, timeout=180)

    # 1. The console entry materialised (the heart of the [project.scripts] change).
    console = venv / "bin" / "ai-hats-tracker"
    assert console.is_file(), f"[project.scripts] must materialise {console}"

    # 2. The EXACT requires.cli.check probe: bare `ai-hats-tracker --version`
    #    resolved on PATH (venv/bin prepended), exit 0.
    probe_env = dict(env)
    probe_env["PATH"] = str(venv / "bin") + os.pathsep + probe_env.get("PATH", "")
    probe = subprocess.run(
        ["ai-hats-tracker", "--version"],
        cwd=str(tmp_path), env=probe_env, capture_output=True, text=True, timeout=60,
    )
    assert probe.returncode == 0, f"`ai-hats-tracker --version` failed:\n{probe.stderr}"
    assert "ai-hats-tracker" in probe.stdout, probe.stdout
