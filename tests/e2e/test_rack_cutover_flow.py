"""e2e: the wired `rack` binary cutover flow (HATS-1038 C1 + C2).

Drives the REAL `rack` console script from a freshly-built launcher venv (so
entry-point discovery runs against real installed metadata) through a git
sandbox: create → plan-gate → execute (worktree) → done (merge-consent), plus a
`context` from INSIDE the task worktree. Fail-under-revert: dropping C1 (the
`ai_hats_rack.kernel_factory` entry point / `rack_cli_provider`) falls `rack`
back to the BARE kernel → no STATE.md refresh, no worktree; dropping C2 (the
resolver gitlink hop) makes `context` from the worktree fail to resolve.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
STATE_MD = Path(".agent") / "ai-hats" / "STATE.md"

_PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _rack(rack: Path, *args: str, cwd: Path, env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(rack), *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=90
    )


def _init_project(root: Path) -> None:
    # Production shape: the tracker is gitignored, so a linked worktree carries
    # neither `.agent/` nor a tracked `ai-hats.yaml` — the C2 resolution case.
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    (root / TASKS_SUB).mkdir(parents=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")


def test_rack_cutover_flow(shared_launcher, tmp_path):
    _launcher, base_env, venv = shared_launcher
    rack = venv / "bin" / "rack"
    assert rack.is_file(), "ai-hats-rack must install the `rack` console script"

    main = tmp_path / "proj"
    main.mkdir()
    _init_project(main)
    env = {**base_env, "AI_HATS_SESSION_ID": "e2e-rack-cutover", "AI_HATS_ROOT_PID": str(os.getpid())}

    # --- C1a: the wired kernel refreshes STATE.md after create (bare does not) ---
    created = _rack(rack, "create", "wired flow", "--role", "assistant", cwd=main, env=env)
    assert created.returncode == 0, created.stderr
    assert "SBX-001" in created.stdout, created.stdout
    state_md = main / STATE_MD
    assert state_md.is_file(), "wired `create` must refresh STATE.md (C1 after_create)"
    assert "SBX-001" in state_md.read_text(encoding="utf-8")

    # --- C1b: the plan-gate fires as a typed abort on an empty plan ---
    planned = _rack(rack, "transition", "SBX-001", "plan", cwd=main, env=env)
    assert planned.returncode == 0, planned.stderr
    gate = _rack(rack, "transition", "SBX-001", "execute", cwd=main, env=env)
    assert gate.returncode == 1, "empty plan must abort the execute transition"
    assert "plan-gate" in (gate.stdout + gate.stderr)
    assert "Traceback" not in gate.stderr, "gate refusal must be typed, not a raw traceback"

    plan_md = main / TASKS_SUB / "SBX-001" / "plan.md"
    plan_md.write_text(plan_md.read_text(encoding="utf-8") + _PLAN_SECTIONS, encoding="utf-8")

    # --- C1c: the wired kernel spins up a worktree on execute + prints the delta ---
    env_ack = {**env, "AI_HATS_PLAN_ACK": "1"}
    executed = _rack(rack, "transition", "SBX-001", "execute", cwd=main, env=env_ack)
    assert executed.returncode == 0, executed.stderr
    wt_lines = [ln for ln in executed.stdout.splitlines() if ln.strip().startswith("Worktree:")]
    assert wt_lines, f"wired `execute` must create + print a worktree (C1)\n{executed.stdout}"
    worktree = Path(wt_lines[0].split("Worktree:", 1)[1].strip())
    assert (worktree / ".git").is_file(), "the delta path must be a real linked worktree"

    # --- C2: `rack context` from INSIDE the worktree resolves the main tracker ---
    from_wt = _rack(rack, "context", "SBX-001", cwd=worktree, env=env)
    assert from_wt.returncode == 0, (
        f"C2 gitlink hop must resolve the main root from the worktree\n{from_wt.stderr}"
    )
    assert "SBX-001" in from_wt.stdout
    assert not (worktree / ".agent").exists(), "resolution must not mkdir a tracker in the worktree"

    # --- C1d: `done` without merge consent is a typed refusal, not a raw traceback ---
    # The launcher-tier env grants consent by default (AI_HATS_MERGE_ACK=1) so
    # other tests can merge; drop it here to exercise the review-consent gate.
    (worktree / "work.txt").write_text("deliverable")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "work")
    _rack(rack, "transition", "SBX-001", "document", cwd=main, env=env)
    _rack(rack, "transition", "SBX-001", "review", cwd=main, env=env)
    no_ack = {k: v for k, v in env.items() if k != "AI_HATS_MERGE_ACK"}
    done = _rack(rack, "transition", "SBX-001", "done", cwd=main, env=no_ack)
    assert done.returncode == 1, "merge without consent must refuse"
    assert "consent" in (done.stdout + done.stderr).lower()
    assert "Traceback" not in done.stderr, "merge-consent refusal must be typed (C1, HATS-1019)"
