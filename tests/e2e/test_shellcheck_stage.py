"""e2e (HATS-1877)

flow:   a maintainer edits shell this repo SHIPS — a git hook or a skill's
        hook, which runs in someone else's project
cmds:
    bash scripts/gates.sh shellcheck
    hooks/merge-gate.sh --stages
expect: the stage is green on this tree and says how many files it read; a
        script with a real warning is refused at severity `warning`; and when
        shellcheck is not installed the stage ANNOUNCES the skip instead of
        passing quietly
why:    the python linters never saw the shell half, and shipped shell fails
        differently: `git_hooks/**` and the skills' `hooks/**` run in a
        consuming project, where a portability bug shows up as a step that
        silently did nothing. HATS-1877 measured the debt at five findings, so
        this stage starts clean rather than with a ratchet
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

STAGE_TIMEOUT_S = 300


def _stage(env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/gates.sh", "shellcheck"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=STAGE_TIMEOUT_S,
    )


def test_the_stage_is_green_on_this_tree():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed on this host")
    run = _stage()
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "[gates] shellcheck" in combined, combined
    assert "file(s) clean" in combined, combined


def test_a_real_warning_is_refused_at_this_severity(tmp_path: Path):
    """Green on a clean tree proves dispatch, not detection."""
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed on this host")
    planted = tmp_path / "planted.sh"
    planted.write_text('#!/usr/bin/env bash\nunused_variable="x"\n', encoding="utf-8")
    run = subprocess.run(
        ["shellcheck", "-S", "warning", str(planted)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = run.stdout + run.stderr
    assert run.returncode == 1, combined
    assert "SC2034" in combined, combined


def test_a_missing_shellcheck_is_announced_not_silent(tmp_path: Path):
    """HATS-1877's own class, aimed back at the stage it added: a check that did
    not run must never read as a pass."""
    shim = tmp_path / "bin"
    shim.mkdir()
    # Everything the dispatcher itself needs to reach the stage, and nothing
    # else — `shellcheck` is what this PATH is built to be missing.
    for tool in ("git", "bash", "tr", "sed", "wc", "cat", "rm", "mktemp", "xargs"):
        found = shutil.which(tool)
        assert found is not None, f"{tool} is a prerequisite of the dispatcher itself"
        (shim / tool).symlink_to(found)

    env = {**os.environ, "PATH": str(shim)}
    run = _stage(env)
    combined = run.stdout + run.stderr

    assert run.returncode == 0, combined
    assert "SKIPPED" in combined, combined
    assert "no shell script was checked" in combined, combined


def test_the_merge_gate_names_the_stage():
    """The gate runs what its `--stages` names, so dropping it here disarms it."""
    hook = (
        "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/maintainer-quality-gate"
        "/hooks/merge-gate.sh"
    )
    listed = subprocess.run(
        ["bash", hook, "--stages"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "shellcheck" in listed.stdout.split(), listed.stdout
