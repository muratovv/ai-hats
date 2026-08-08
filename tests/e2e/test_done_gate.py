"""e2e: the done-gate refuses a real ``review → done`` transition (HATS-1137).

The REAL ``rack`` binary drives a git sandbox through the real FSM to ``review``
with a real task worktree, against the REAL ``hooks/done-gate.sh`` bytes — copied
into the sandbox's own ``libraries/``, which wins ``find_component_dir``'s
last-wins search and keeps the composed script's nearest ``.git`` a directory
(``check_resolve._reject_worktree_root`` refuses a linked worktree's ``.git``
FILE).

Fail-under-revert: :func:`test_removing_the_checks_row_lets_the_red_card_through`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from _helpers.git import git, init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = "maintainer-quality-gate"
SKILL_SRC = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library" / "usage" / "skills" / SKILL
#: The role that carries the shipped binding — read, never edited, by this file.
MAINTAINER_ROLE = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/usage/roles/maintainer/config.yaml"
)

TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
EDGE = "edge:review--done"
SCRIPT = "hooks/done-gate.sh"
#: ``<event>~<skill>~<script>.log`` — one file per (task, edge, binding), the
#: script's ``/`` escaped to ``+`` (``rack_consumers._escaped``, HATS-1137).
EDGE_LOG = f"edge-review--done~{SKILL}~hooks+done-gate.sh.log"
GATE_MARKER_DIR = Path(".git") / "ai-hats" / "done-gate"

#: Enough plan.md for the packaged plan-gate to let `execute` through.
PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)

_PROJECT_YAML = """\
schema_version: 4
provider: claude
default_role: {role}
task_prefix: SBX
ai_hats_dir: .agent/ai-hats
"""

_UNGATED_ROLE = f"""\
name: ungated
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills:
    - {SKILL}
injection: |
  # ROLE: UNGATED
"""

#: A `ci-local.sh` the gate can find. `--check` only proves it EXISTS (a project
#: with no done-gate stage could never earn a marker honestly); it is never run
#: from inside the lock, so a stub is the honest shape here.
_CI_LOCAL_STUB = '#!/usr/bin/env bash\necho "[stub] stage=${1:-}" >&2\nexit 0\n'


def shipped_binding() -> dict:
    """The ``checks:`` row this sandbox binds.

    It used to be read out of the ``maintainer`` role so the suite could not
    drift from what the library ships. HATS-1538 withdrew that row — role scope
    is not backlog scope, so a shipped binding fired on every scratch tasks-dir
    the rack CLI touched — and there is now nothing to read. Kept as a literal,
    and ``test_the_maintainer_role_ships_no_checks_row`` below is what stops the
    two from silently diverging again: re-couple this to the library in the same
    change that re-lands the row.
    """
    return {"skill": SKILL, "script": SCRIPT, "on": [EDGE], "on_error": "refuse"}


# ---------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------


def _seed_library(project: Path, *, bind: bool) -> None:
    """Project-local library: the real skill, plus a gated and an ungated role."""
    lib = project / "libraries"
    shutil.copytree(SKILL_SRC, lib / "skills" / SKILL)

    gated = {
        "name": "gated",
        "priorities": ["Reliability"],
        "composition": {"traits": [], "rules": [], "skills": [SKILL]},
        "injection": "# ROLE: GATED\n",
    }
    if bind:
        gated["composition"]["checks"] = [shipped_binding()]
    (lib / "roles" / "gated").mkdir(parents=True)
    (lib / "roles" / "gated" / "config.yaml").write_text(
        yaml.safe_dump(gated, sort_keys=False), encoding="utf-8"
    )

    (lib / "roles" / "ungated").mkdir(parents=True)
    (lib / "roles" / "ungated" / "config.yaml").write_text(_UNGATED_ROLE, encoding="utf-8")


@pytest.fixture
def gate_project(shared_launcher, tmp_path: Path):
    """Factory: ``(project, env)`` for a git sandbox whose active role is ``role``.

    No ``AI_HATS_SESSION_ID``: this is the live-resolution mode (ADR-0019 D9
    clause 3), so the bytes come from the project's own library rather than from
    a session snapshot this test would otherwise have to plant.
    """
    _launcher, base_env, _venv = shared_launcher
    counter = {"n": 0}

    def make(role: str, *, bind: bool = True) -> tuple[Path, dict[str, str]]:
        counter["n"] += 1
        project = tmp_path / f"proj{counter['n']}"
        project.mkdir()
        # A MAIN checkout: `.git` must be a directory, or D9 clause 4 refuses
        # before any check runs.
        init_repo(project, branch="master", harden=True)
        (project / "ai-hats.yaml").write_text(_PROJECT_YAML.format(role=role), encoding="utf-8")
        (project / ".gitignore").write_text(".agent/\n", encoding="utf-8")
        (project / TASKS_SUB).mkdir(parents=True)
        (project / "scripts").mkdir()
        (project / "scripts" / "ci-local.sh").write_text(_CI_LOCAL_STUB, encoding="utf-8")
        _seed_library(project, bind=bind)
        git(project, "add", "-A")
        git(project, "commit", "-m", "seed")

        env = {k: v for k, v in base_env.items() if k != "AI_HATS_SESSION_ID"}
        env["AI_HATS_USER_HOME"] = str(tmp_path / f"home{counter['n']}")
        Path(env["AI_HATS_USER_HOME"]).mkdir()
        return project, env

    return make


# ---------------------------------------------------------------------------
# driving the real binary
# ---------------------------------------------------------------------------


def _rack(rack: Path, *args: str, cwd: Path, env: dict[str, str]):
    return subprocess.run(  # noqa: S603 - binary from the shared-launcher fixture
        [str(rack), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture
def rack_bin(shared_launcher) -> Path:
    _launcher, _env, venv = shared_launcher
    rack = venv / "bin" / "rack"
    assert rack.is_file(), "ai-hats-rack must install the `rack` console script"
    return rack


def _card(project: Path, task_id: str) -> Path:
    return project / TASKS_SUB / task_id / "task.yaml"


def _state(project: Path, task_id: str) -> dict:
    return yaml.safe_load(_card(project, task_id).read_text(encoding="utf-8"))


def _check_log(project: Path, task_id: str) -> Path:
    return project / TASKS_SUB / task_id / ".checks" / EDGE_LOG


def _reason(result: subprocess.CompletedProcess[str]) -> str:
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "aborted", payload
    assert payload["error"]["subscriber"] == "checks", payload
    return payload["error"]["reason"]


def _to_review(
    rack: Path, project: Path, env: dict[str, str], *, worktree: bool
) -> tuple[str, str]:
    """Drive a fresh card to ``review``. Returns ``(task_id, worktree_path)``.

    ``worktree=False`` takes the forced arrow straight to ``review`` — the card
    that never entered ``execute`` and so never got a worktree (F-11).
    """
    created = _rack(rack, "create", "gate probe", cwd=project, env=env)
    assert created.returncode == 0, created.stderr
    match = re.search(r"Created: (\S+)", created.stdout)
    assert match, created.stdout
    task_id = match.group(1)

    if not worktree:
        forced = _rack(
            rack,
            "transition",
            task_id,
            "review",
            "--force",
            "--reason",
            "no-code card",
            cwd=project,
            env=env,
        )
        assert forced.returncode == 0, forced.stdout + forced.stderr
        return task_id, ""

    assert _rack(rack, "transition", task_id, "plan", cwd=project, env=env).returncode == 0
    plan_md = project / TASKS_SUB / task_id / "plan.md"
    plan_md.write_text(plan_md.read_text(encoding="utf-8") + PLAN_SECTIONS, encoding="utf-8")

    executed = _rack(
        rack, "transition", task_id, "execute", cwd=project, env={**env, "AI_HATS_PLAN_ACK": "1"}
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    lines = [ln for ln in executed.stdout.splitlines() if ln.strip().startswith("Worktree:")]
    assert lines, f"execute must create a worktree\n{executed.stdout}"
    wt = Path(lines[0].split("Worktree:", 1)[1].strip())

    # Real deliverable, so the branch tip is a commit of its own.
    (wt / "work.txt").write_text("deliverable", encoding="utf-8")
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "work")

    for state in ("document", "review"):
        moved = _rack(rack, "transition", task_id, state, cwd=project, env=env)
        assert moved.returncode == 0, moved.stdout + moved.stderr
    return task_id, str(wt)


def _write_marker(project: Path, sha: str) -> Path:
    """Plant a marker the way ``lib/gate-marker.sh`` writes one."""
    marker_dir = project / GATE_MARKER_DIR
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / sha
    marker.write_text(f"sha={sha}\ntimestamp=2026-01-01T00:00:00Z\nstage=done-gate\n", "utf-8")
    return marker


# ---------------------------------------------------------------------------
# the shipped binding
# ---------------------------------------------------------------------------


def test_the_maintainer_role_ships_no_checks_row():
    """HATS-1538: the row stays withdrawn until scoping is ruled on.

    A role-scoped binding fires on EVERY backlog the rack CLI touches, not only
    the project's own — so the shipped row refused scratch tasks-dirs, including
    the ones this repo's rack tests build. Re-landing it before that is answered
    turns `make check` red again, and the symptom surfaces two subsystems away
    (the card strands in `review`, and the NEXT transition fails naming
    plan-gate). This test is the tombstone that makes the return deliberate.
    """
    config = yaml.safe_load(MAINTAINER_ROLE.read_text(encoding="utf-8"))
    assert "checks" not in config["composition"], (
        "the maintainer role carries a checks: row again — HATS-1538 S2 must rule "
        "on backlog scope and on a session that predates a binding first"
    )


def test_the_maintainer_role_is_ai_hats_specific_not_generic():
    """The gate refuses any project with no ``done-gate`` stage, so the role that
    carries it must be one only this codebase wears."""
    text = MAINTAINER_ROLE.read_text(encoding="utf-8")
    assert "/usage/roles/" in MAINTAINER_ROLE.as_posix(), "usage/ is the project-specific layer"
    assert "ai-hats-maintainer" in text
    assert "ai-hats codebase" in text


# ---------------------------------------------------------------------------
# 1. no marker — the refusal (R1, R5, R6)
# ---------------------------------------------------------------------------


def test_a_branch_with_no_marker_cannot_reach_done(gate_project, rack_bin):
    """R1: the card stays in review, the card file is byte-unchanged, and the
    reason carries the command that clears the gate."""
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)
    before = _card(project, task_id).read_bytes()

    refused = _rack(rack_bin, "transition", task_id, "done", cwd=project, env=env)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert _card(project, task_id).read_bytes() == before, "a refused edge must persist nothing"
    assert _state(project, task_id)["state"] == "review"
    assert f"{EDGE} aborted by 'checks'" in refused.stderr
    assert "Traceback" not in refused.stderr, "a refusal must be typed, not a stack"

    as_json = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)
    reason = _reason(as_json)
    assert "no green quality-gate marker" in reason
    # R6: an action, not a diagnosis — the exact command, in the right directory.
    assert f"cd {wt} && make done-gate" in reason
    assert _card(project, task_id).read_bytes() == before

    # The merge never happened: no work.txt on master.
    assert not (project / "work.txt").exists()


# ---------------------------------------------------------------------------
# 2. the marker clears it — R6's second half
# ---------------------------------------------------------------------------


def test_a_marker_for_the_branch_tip_lets_the_transition_through(gate_project, rack_bin):
    """The same transition, the same card, one marker later."""
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)

    blocked = _rack(rack_bin, "transition", task_id, "done", cwd=project, env=env)
    assert blocked.returncode == 1, "positive control: unmarked must refuse first"

    tip = git(Path(wt), "rev-parse", "HEAD").stdout.strip()
    _write_marker(project, tip)

    taken = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)

    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "done"
    assert "green marker present" in _check_log(project, task_id).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 3. the key is content, not time — R4
# ---------------------------------------------------------------------------


def test_a_marker_for_a_different_sha_does_not_clear_the_gate(gate_project, rack_bin):
    """R4: a marker earned on an earlier commit stops applying the moment the
    branch moves — no expiry mechanism exists because none is needed."""
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)

    stale = git(Path(wt), "rev-parse", "HEAD").stdout.strip()
    _write_marker(project, stale)
    # The branch moves on: the marker now describes content that is not the tip.
    (Path(wt) / "more.txt").write_text("second thought", encoding="utf-8")
    git(Path(wt), "add", "-A")
    git(Path(wt), "commit", "-m", "more work")
    tip = git(Path(wt), "rev-parse", "HEAD").stdout.strip()
    assert tip != stale

    refused = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reason = _reason(refused)
    assert tip in reason, "the refusal must name the tip it wanted, not the marker it found"
    assert _state(project, task_id)["state"] == "review"


# ---------------------------------------------------------------------------
# 4. a card with no worktree is not taxed — F-11
# ---------------------------------------------------------------------------


def test_a_card_with_no_worktree_passes(gate_project, rack_bin):
    """F-11: the subject of the gate is the code entering master through this
    card. A doc/research card brings none, so it pays nothing."""
    project, env = gate_project("gated")
    task_id, _ = _to_review(rack_bin, project, env, worktree=False)

    taken = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)

    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "done"
    # Non-vacuous: the gate RAN and said pass, rather than never being reached.
    assert "has no worktree" in _check_log(project, task_id).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. role scope — ADR-0019 D7
# ---------------------------------------------------------------------------


def test_a_role_that_does_not_bind_the_gate_is_not_gated(gate_project, rack_bin):
    """D7: bindings are role-scoped. The `ungated` role composes the very same
    skill and still takes the edge untaxed — which the retired union-scoped
    ``lifecycle_hooks`` could not have expressed."""
    project, env = gate_project("ungated")
    task_id, _ = _to_review(rack_bin, project, env, worktree=True)

    taken = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)

    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "done"
    assert not _check_log(project, task_id).parent.exists(), "an unbound role must run no check"


# ---------------------------------------------------------------------------
# 6. fail-under-revert — the rejection criterion
# ---------------------------------------------------------------------------


def test_removing_the_checks_row_lets_the_red_card_through(gate_project, rack_bin):
    """Take the ``checks:`` row away and case 1 flips from refused to done.

    A gate whose test still passes with the gate removed is not a gate. Same
    sandbox shape, same unmarked branch, only the row is gone.
    """
    project, env = gate_project("gated", bind=False)
    task_id, _ = _to_review(rack_bin, project, env, worktree=True)

    role = project / "libraries" / "roles" / "gated" / "config.yaml"
    assert "checks" not in role.read_text(encoding="utf-8"), "the row must really be gone"

    taken = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)

    assert taken.returncode == 0, (
        "with the checks row removed the unmarked branch MUST reach done — "
        f"if it still refuses, case 1 was never proving the gate\n{taken.stdout}{taken.stderr}"
    )
    assert json.loads(taken.stdout)["task"]["state"] == "done"
    assert not _check_log(project, task_id).parent.exists()
