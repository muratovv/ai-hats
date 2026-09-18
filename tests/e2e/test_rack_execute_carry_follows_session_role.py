"""e2e (HATS-2003)

flow:   a session of one role takes a card filed under another role into
        execute, and the new worktree must be provisioned for the role that is
        actually entering it
cmds:
    AI_HATS_PLAN_ACK=1 rack transition T-1 execute
    ai-hats wt create task/probe
expect: the wt_in hook the SESSION's role composes runs (its marker lands on
        disk) and the card's work log names that role; a hook only the CARD's
        role composes does not run; with both roles equal the marker lands —
        the positive control on the fixture
why:    a card filed under behaviorist by a role-curator session got a worktree
        provisioned for behaviorist: no worktree-venv, no .venv, and nothing
        said which role it had been provisioned for
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from _helpers.env import checkout_pythonpath, clean_env
from _helpers.git import git, init_repo
from _helpers.sessions import stand_in_session

pytestmark = [pytest.mark.integration, pytest.mark.rack, pytest.mark.wt]

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "wt_hook_lib"
TASKS_SUB = Path(".agent/ai-hats/tracker/backlog/tasks")

HOOK_ROLE = "e2e-wthook-role"  # composes the fixture skill with a wt_in hook
PLAIN_ROLE = "e2e-wthook-plain-role"  # composes nothing

PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git project whose configured role composes no worktree hook."""
    p = tmp_path / "project"
    p.mkdir()
    init_repo(p, branch="master")
    shutil.copytree(FIXTURE_LIB, p / "libraries")
    (p / "ai-hats.yaml").write_text(
        f"schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
        f"task_prefix: T\ndefault_role: {PLAIN_ROLE}\n",
        encoding="utf-8",
    )
    (p / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    (p / TASKS_SUB).mkdir(parents=True)
    git(p, "add", "-A")
    git(p, "commit", "-m", "seed")
    return p


def _env(project: Path, *, session_role: str | None) -> dict[str, str]:
    # A clean base: the launching session's own AI_HATS_DIR pin would route
    # `rack create` into THAT project's backlog, not the sandbox's.
    env = clean_env(os.environ)
    for key in ("AI_HATS_SESSION_IDENTITY", "AI_HATS_ROLE"):
        env.pop(key, None)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env["AI_HATS_USER_HOME"] = str(project.parent / "home")
    Path(env["AI_HATS_USER_HOME"]).mkdir(exist_ok=True)
    env["AI_HATS_PLAN_ACK"] = "1"
    if session_role is not None:
        stand_in_session(env, project, "s-carry", role=session_role)
    return env


def _run(module: str, *args: str, cwd: Path, env: dict[str, str]):
    return subprocess.run(  # noqa: S603 — the interpreter under test, fixed argv
        [sys.executable, "-m", module, *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _execute(project: Path, *, card_role: str, session_role: str | None) -> str:
    """Drive a fresh card to execute; return the transition's combined output."""
    env = _env(project, session_role=session_role)
    created = _run(
        "ai_hats_rack", "create", "probe", "--id", "T-1", "--role", card_role, cwd=project, env=env
    )
    assert created.returncode == 0, created.stdout + created.stderr
    planned = _run("ai_hats_rack", "transition", "T-1", "plan", cwd=project, env=env)
    assert planned.returncode == 0, planned.stdout + planned.stderr
    plan_md = project / TASKS_SUB / "T-1" / "plan.md"
    plan_md.write_text(plan_md.read_text(encoding="utf-8") + PLAN_SECTIONS, encoding="utf-8")
    executed = _run("ai_hats_rack", "transition", "T-1", "execute", cwd=project, env=env)
    out = executed.stdout + executed.stderr
    assert executed.returncode == 0, out
    assert "Worktree:" in out, out
    return out


def _work_log(project: Path) -> str:
    card = yaml.safe_load((project / TASKS_SUB / "T-1" / "task.yaml").read_text(encoding="utf-8"))
    return "\n".join(str(entry) for entry in card.get("work_log", []))


def test_execute_worktree_runs_the_sessions_wt_in_hook(project: Path):
    """Card says plain, session says hook role: the session's hook runs."""
    _execute(project, card_role=PLAIN_ROLE, session_role=HOOK_ROLE)

    seeded = project / ".seeded"
    assert seeded.exists(), "the session role's wt_in hook did not run"
    assert Path(seeded.read_text().strip()).is_dir(), seeded.read_text()
    assert HOOK_ROLE in _work_log(project), _work_log(project)


def test_execute_worktree_does_not_run_the_cards_role_hooks(project: Path):
    """The mirror: card says hook role, session says plain — nothing runs.

    A carry that still read the card would write the marker here.
    """
    _execute(project, card_role=HOOK_ROLE, session_role=PLAIN_ROLE)

    assert not (project / ".seeded").exists(), "the CARD's role provisioned the worktree"
    assert PLAIN_ROLE in _work_log(project), _work_log(project)


def test_fixture_control_same_role_on_both_sides_runs_the_hook(project: Path):
    """Positive control on the fixture: agreeing roles write the marker, so an
    absent marker under a mismatch is the defect, not a broken hook path."""
    _execute(project, card_role=HOOK_ROLE, session_role=HOOK_ROLE)

    assert (project / ".seeded").exists()


def test_wt_create_runs_the_sessions_wt_in_hook(project: Path):
    """The direct road: `ai-hats wt create` provisions for the session too."""
    env = _env(project, session_role=HOOK_ROLE)

    created = _run("ai_hats", "wt", "create", "task/probe", cwd=project, env=env)
    assert created.returncode == 0, created.stdout + created.stderr

    assert (project / ".seeded").exists(), "the session role's wt_in hook did not run"
