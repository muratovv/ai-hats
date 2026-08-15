"""e2e (HATS-1598)

flow:   an agy tool call whose PreToolUse hook hangs instead of answering
cmds:
    sh -c '... "$AI_HATS_PYTHON" -m ai_hats_agy.hook_dispatcher "$@"' sh PreToolUse Edit
expect: the hook is killed at its budget and the dispatcher returns 1 (BROKE per
        ADR-0020 D2), naming the hook on stderr
why:    the dispatcher runs on EVERY tool call, so an unbounded hook wedges the
        whole session — one typo in a gate script and no tool ever returns
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_hats.session_identity import SessionIdentity
from ai_hats_agy.global_hook import DISPATCHER_COMMAND

pytestmark = pytest.mark.integration

SESSION_ID = "e2e-sid-timeout"
BUDGET_S = 2.0


def _identity(project: Path) -> SessionIdentity:
    """The envelope the launch would hand the dispatcher (HATS-1594).

    Built from the production dataclass rather than a literal JSON blob so a
    change to the envelope reaches this fixture as a type error, not as four
    silently unreachable hooks.
    """
    return SessionIdentity(
        id=SESSION_ID,
        role="assistant",
        provider="agy",
        project_dir=project,
        session_dir=project / ".agent" / "ai-hats" / "sessions" / "runs" / SESSION_ID,
    )


@pytest.fixture
def hanging_hook(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A session whose PreToolUse hook never returns; yields (project, env)."""
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()

    hook = tmp_path / "hangs.sh"
    hook.write_text("#!/bin/sh\nsleep 300\n")
    hook.chmod(0o755)
    (cache / "hooks.json").write_text(
        json.dumps({"PreToolUse": [{"matcher": "Edit", "command": str(hook)}]})
    )

    env = {
        **os.environ,
        # HATS-1594: the envelope is read before the hooks, so a bare session id
        # reads as a session too old to say what it is and nothing fires.
        **_identity(project).to_env(),
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
        "AI_HATS_PYTHON": sys.executable,
        "AI_HATS_AGY_HOOK_TIMEOUT_S": str(BUDGET_S),
    }
    return project, env


def test_hanging_hook_is_killed_and_reported_broke(hanging_hook) -> None:
    """Without a bound this call never returns — pytest's own timeout is what
    ends the test, which is exactly what an agent's tool call experiences.

    Exit 1 is the D2 verdict for a timeout: the hook was killed before it could
    form one, so it broke rather than refused. The stderr assertion is what
    keeps this test honest — any crash also exits 1.
    """
    project, env = hanging_hook

    started = time.monotonic()
    proc = subprocess.run(
        ["sh", "-c", DISPATCHER_COMMAND, "sh", "PreToolUse", "Edit"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    elapsed = time.monotonic() - started

    assert proc.returncode == 1, (
        f"expected 1 (BROKE), got {proc.returncode}; stderr:\n{proc.stderr[-300:]}"
    )
    assert "timed out" in proc.stderr and "hangs.sh" in proc.stderr, (
        f"a killed hook must name itself and the budget: {proc.stderr[-300:]}"
    )
    assert elapsed < 30.0, (
        f"returned after {elapsed:.1f}s on a {BUDGET_S:g}s budget — the bound is "
        f"not the one the session asked for"
    )


def test_prompt_hook_still_runs_to_completion(hanging_hook) -> None:
    """The bound must not clip hooks that answer — the regression that would
    make a timeout look 'fixed' by refusing everything."""
    project, env = hanging_hook
    cache = Path(env["AI_HATS_SESSION_CACHE_DIR"])
    marker = cache.parent / "fired.txt"
    quick = cache.parent / "quick.sh"
    quick.write_text(f"#!/bin/sh\necho FIRED > '{marker}'\n")
    quick.chmod(0o755)
    (cache / "hooks.json").write_text(
        json.dumps({"PreToolUse": [{"matcher": "Edit", "command": str(quick)}]})
    )

    proc = subprocess.run(
        ["sh", "-c", DISPATCHER_COMMAND, "sh", "PreToolUse", "Edit"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, f"a fast hook was refused: {proc.stderr[-300:]}"
    assert marker.is_file(), f"hook never fired; stderr:\n{proc.stderr[-300:]}"
