"""e2e (HATS-1969)

flow:   a person grants rack.transition in a candidate session that declares consent and no scripts
cmds:
    consent rack.transition 30
    rack transition SBX-1 execute
expect: without a grant the wrapper refuses and the card stays in plan; after the
        verb the same transition moves the card to execute
why:    maintainer also arms script gates, and those refuse inside a linked worktree,
        so a passed approve never shows whether consent itself lets the command run
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath
from _helpers.git import git as _git

pytestmark = [pytest.mark.integration, pytest.mark.consent]

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
SESSION_ID = "e2e-re-consent"
EXPR = (
    "rack.transition::plan-execute + rack.transition::done"
    " + wt.merge::pre-merge + wt.discard::pre-discard + fs.remove::critical-path"
)
_PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)


def _candidate(project: Path):
    from ai_hats.composition_seam import build_composition_payload

    payload = build_composition_payload(
        project,
        role_override=EXPR,
        provider_name="claude",
        interactive=True,
        experimental=True,
    )
    plan = payload.plan
    assert plan is not None and plan.dsl == "re"
    assert plan.hooks.runtime == ()
    assert plan.hooks.external and all(hook.run is None for hook in plan.hooks.external)
    return plan


def _env(project: Path, checkout_bin: Path) -> dict[str, str]:
    from ai_hats.consent_wrapper import CONFIG_ENV, CONSENT_APP
    from ai_hats.session_identity import SessionIdentity, drop_identity
    from ai_hats.session_report import consent_row
    from ai_hats.surfaces.claude.provider import ClaudeSurface

    from _helpers.sessions import build_session

    composition = _candidate(project)
    env = os.environ.copy()
    drop_identity(env)
    env.pop(CONFIG_ENV, None)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    env["AI_HATS_ROOT_PID"] = str(os.getpid())
    kept = [
        entry for entry in env.get("PATH", "").split(os.pathsep) if "consent-wrapper" not in entry
    ]
    env["PATH"] = os.pathsep.join([str(checkout_bin), *kept])

    armed = build_session(
        project,
        composition,
        ClaudeSurface(),
        SESSION_ID,
        environ=env,
        middleware=True,
    )
    env.update(armed.env)
    session_dir = project / ".agent" / "ai-hats" / "sessions" / "runs" / SESSION_ID
    env.update(
        SessionIdentity(
            id=SESSION_ID,
            role=EXPR,
            provider="claude",
            project_dir=project,
            session_dir=session_dir,
            session_cache_dir=str(armed.root),
            dsl="re",
        ).to_env()
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    rows = [consent_row(hook) for hook in composition.hooks.external if hook.app == CONSENT_APP]
    (session_dir / "role_materialization.json").write_text(
        json.dumps({"role": EXPR, "checks": [], "consent": rows}),
        encoding="utf-8",
    )
    return env


def _run(project: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - PATH selects the session wrapper
        args,
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _state(project: Path, env: dict[str, str], task_id: str) -> str:
    seen = _run(project, env, "rack", "context", task_id)
    assert seen.returncode == 0, seen.stderr or seen.stdout
    for line in seen.stdout.splitlines():
        if line.strip().startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(seen.stdout)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\nprovider: claude\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    (root / TASKS_SUB).mkdir(parents=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")
    return root


@pytest.fixture
def env(project: Path, checkout_bin: Path) -> dict[str, str]:
    return _env(project, checkout_bin)


def test_the_expression_arms_consent_and_no_script(project: Path) -> None:
    hooks = _candidate(project).hooks.external
    assert {hook.object for hook in hooks} == {
        "rack.transition",
        "wt.merge",
        "wt.discard",
        "fs.remove",
    }


def test_a_grant_lets_the_transition_run(project: Path, env: dict[str, str]) -> None:
    # HATS-1969 — consent terms alone; the catalog reads the body, not the header.
    created = _run(project, env, "rack", "create", "consent only")
    assert created.returncode == 0, created.stderr or created.stdout
    task_id = next(word for word in created.stdout.split() if word.startswith("SBX-"))
    assert _run(project, env, "rack", "transition", task_id, "plan").returncode == 0
    plan_md = project / TASKS_SUB / task_id / "plan.md"
    plan_md.write_text(plan_md.read_text(encoding="utf-8") + _PLAN_SECTIONS, encoding="utf-8")

    refused = _run(project, env, "rack", "transition", task_id, "execute")
    assert refused.returncode != 0, refused.stdout
    assert "requires supervisor approval" in refused.stderr, refused.stderr or refused.stdout
    assert _state(project, env, task_id) == "plan"

    granted = _run(project, env, "consent", "rack.transition", "30")
    assert granted.returncode == 0, granted.stderr or granted.stdout

    moved = _run(project, env, "rack", "transition", task_id, "execute")
    assert moved.returncode == 0, moved.stderr or moved.stdout
    assert _state(project, env, task_id) == "execute"
