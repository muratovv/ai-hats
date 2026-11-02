"""e2e (HATS-1969)

flow:   a person in a candidate maintainer session moves a card to done
cmds:
    rack transition SBX-1 done
expect: the card stays in execute and the refusal names make done-gate; the
        library the gate runs from is not the linked worktree
why:    a candidate session arms the same workflow gate as the shipped role, and
        a linked worktree is refused before that script can run
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath, consented
from _helpers.git import git, init_repo

pytestmark = [pytest.mark.integration, pytest.mark.gates, pytest.mark.library]

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
SESSION_ID = "e2e-re-gates"
_PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)


def _library_outside_any_checkout(dest: Path) -> Path:
    src = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    assert not (dest / ".git").exists()
    return dest


def _project(path: Path) -> None:
    path.mkdir()
    init_repo(path, branch="master")
    (path / "ai-hats.yaml").write_text("task_prefix: SBX\nprovider: claude\n", encoding="utf-8")
    (path / ".gitignore").write_text(".agent/\nai-hats.yaml\n", encoding="utf-8")
    (path / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "gates.sh", path / "scripts" / "gates.sh")
    git(path, "add", "-A")
    git(path, "commit", "-m", "seed")


def _env(project: Path, checkout_bin: Path, library: Path) -> dict[str, str]:
    from ai_hats.composition_seam import build_composition_payload
    from ai_hats.consent_wrapper import CONFIG_ENV, CONSENT_APP
    from ai_hats.session_identity import SessionIdentity, drop_identity
    from ai_hats.session_report import consent_row
    from ai_hats.surfaces.claude.provider import ClaudeSurface
    from ai_hats_core.layout import ProjectLayout

    from _helpers.sessions import build_session

    os.environ["AI_HATS_LIBRARY_ROOT"] = str(library)
    payload = build_composition_payload(
        project,
        role_override="maintainer",
        provider_name="claude",
        interactive=True,
        experimental=True,
    )
    plan = payload.plan
    assert plan is not None and plan.dsl == "re"
    done = [
        hook
        for hook in plan.hooks.external
        if hook.app == "rack" and hook.at == "->done" and hook.run is not None
    ]
    assert done and done[0].on_error is not None and done[0].on_error.value == "refuse"
    assert library in done[0].run.path.parents

    env = os.environ.copy()
    drop_identity(env)
    env.pop(CONFIG_ENV, None)
    env["AI_HATS_LIBRARY_ROOT"] = str(library)
    env["AI_HATS_USER_HOME"] = str(project.parent / "home")
    Path(env["AI_HATS_USER_HOME"]).mkdir(exist_ok=True)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    env["AI_HATS_ROOT_PID"] = str(os.getpid())
    kept = [
        entry for entry in env.get("PATH", "").split(os.pathsep) if "consent-wrapper" not in entry
    ]
    env["PATH"] = os.pathsep.join([str(checkout_bin), *kept])

    surface = ClaudeSurface()
    armed = build_session(project, plan, surface, SESSION_ID, environ=env, middleware=True)
    skills_root = surface.session_skills_root(ProjectLayout.at(project), SESSION_ID)
    assert (skills_root / "quality-gate" / "hooks" / "done-gate.sh").is_file()
    env.update(armed.env)
    session_dir = project / ".agent" / "ai-hats" / "sessions" / "runs" / SESSION_ID
    env.update(
        SessionIdentity(
            id=SESSION_ID,
            role="maintainer",
            provider="claude",
            project_dir=project,
            session_dir=session_dir,
            session_cache_dir=str(armed.root),
            skills_root=str(skills_root),
            dsl="re",
        ).to_env()
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    rows = [consent_row(hook) for hook in plan.hooks.external if hook.app == CONSENT_APP]
    (session_dir / "role_materialization.json").write_text(
        json.dumps({"role": "maintainer", "checks": [], "consent": rows}),
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


def test_done_from_a_candidate_session_hits_the_gate(tmp_path: Path, checkout_bin: Path) -> None:
    # HATS-1969 — the catalog reads the body; the header pin alone is not a basis.
    library = _library_outside_any_checkout(tmp_path / "library")
    project = tmp_path / "proj"
    _project(project)
    env = _env(project, checkout_bin, library)

    created = _run(project, env, "rack", "create", "gate probe")
    assert created.returncode == 0, created.stderr or created.stdout
    task_id = next(word for word in created.stdout.split() if word.startswith("SBX-"))
    assert _run(project, env, "rack", "transition", task_id, "plan").returncode == 0
    plan_md = project / TASKS_SUB / task_id / "plan.md"
    plan_md.write_text(plan_md.read_text(encoding="utf-8") + _PLAN_SECTIONS, encoding="utf-8")

    executed = _run(
        project,
        consented(env, AI_HATS_PLAN_ACK="1"),
        "rack",
        "transition",
        task_id,
        "execute",
    )
    assert executed.returncode == 0, executed.stderr or executed.stdout
    assert _state(project, env, task_id) == "execute"

    refused = _run(
        project,
        consented(env),
        "rack",
        "transition",
        task_id,
        "done",
        "--force",
        "--reason",
        "probe",
    )
    assert refused.returncode != 0, refused.stdout
    assert "linked worktree" not in refused.stderr
    assert "make done-gate" in refused.stderr, refused.stderr or refused.stdout
    assert _state(project, env, task_id) == "execute"
