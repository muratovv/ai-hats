"""e2e (HATS-1137)

flow:   an agent transitioning a task card to done state
cmds:
    rack transition HATS-1137 done
expect: done gate verifies review approval, documentation completeness, and e2e catalog
        freshness before allowing transition
why:    without done gates, agents transition unreviewed, undocumented, or catalog-stale
        task cards directly to done
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from _helpers.env import consented
from _helpers.git import git, init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = "maintainer-quality-gate"
SKILL_SRC = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library" / "ai-hats-dev" / "skills" / SKILL
)
#: The role that carries the shipped binding — read, never edited, by this file.
MAINTAINER_ROLE = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/roles/maintainer/config.yaml"
)

TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
EDGE = "review->done"
#: ONE hook on every edge since HATS-1878; the row's `gate:` cargo says which.
SCRIPT = "hooks/gate.sh"
#: ``<event>~<skill>~<script>.log`` — one file per (task, edge, binding), the
#: script's ``/`` escaped to ``+`` (``rack_consumers._escaped``, HATS-1137).
#: One log per (task, point, row). The row identity carries the app and the
#: backlog since HATS-1545, so two backlogs binding one script cannot collide.
#: The readable stem of the check-log name; ``check_log_token`` appends an
#: identity digest so two rows can never share a file (HATS-1137).
#: The log is named after the EVENT that fired, not after the selector the row
#: binds: the row says `->done`, the event on this road is `review->done`. The
#: arrow is escaped for a filename (HATS-1719) because it carries `>`, a shell
#: redirect, and a refusal hands this path to an operator to paste.
EDGE_LOG_STEM = f"review-%3Edone~rack~tasks~{SKILL}~hooks+gate.sh"
#: One marker per STAGE per tree (HATS-1878); a gate passes when its whole set is there.
STAGES_DIR = Path(".git") / "ai-hats" / "stages"
#: The project's side of the gate, copied into every sandbox (ADR-0023 D7).
PROJECT_SCRIPTS = ("gates.sh", "ci-gate.sh")

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

#: A stage runner the primitive can find. The stages never run from inside the
#: lock — a check is a marker lookup — so a stub is the honest shape here.
_CI_LOCAL_STUB = '#!/usr/bin/env bash\necho "[stub] stage=${1:-}" >&2\nexit 0\n'


def gate_stages(gate: str) -> list[str]:
    """What the table requires of a gate, asked of the table itself."""
    out = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "gates.sh"), "stages", gate],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def shipped_apps() -> dict:
    """The ``composition.apps`` block the ``maintainer`` role actually ships.

    Read from the library rather than restated here, so the sandbox exercises
    the rows under review — a hand-copied literal drifts from them in silence,
    and HATS-1538 proved the drift is what survives. Since HATS-1545 the block
    is carried whole: its shape below ``apps.<app>`` belongs to the app, so a
    test that picked rows apart would be re-implementing a grammar it does not
    own.
    """
    config = yaml.safe_load(MAINTAINER_ROLE.read_text(encoding="utf-8"))
    return config["composition"]["apps"]


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
        gated["composition"]["apps"] = shipped_apps()
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
        for name in PROJECT_SCRIPTS:
            shutil.copy(REPO_ROOT / "scripts" / name, project / "scripts" / name)
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


def _checks_dir(project: Path, task_id: str) -> Path:
    """Where a check's log would land — asserted ABSENT where no gate should run."""
    return project / TASKS_SUB / task_id / ".checks"


def _sole_log(checks_dir: Path) -> Path:
    """The one log under ``checks_dir`` whose stem is this gate's."""
    found = sorted(p for p in checks_dir.glob("*.log") if p.name.rsplit("~", 1)[0] == EDGE_LOG_STEM)
    assert len(found) == 1, f"expected one {EDGE_LOG_STEM}* log, got {[p.name for p in found]}"
    return found[0]


def _check_log(project: Path, task_id: str) -> Path:
    return _sole_log(project / TASKS_SUB / task_id / ".checks")


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


def _tree(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "rev-parse", f"{rev}^{{tree}}").stdout.strip()


def _write_marker(project: Path, tree: str, stages: list[str] | None = None) -> Path:
    """Plant markers the way ``scripts/ci-gate.sh`` writes them: one file per
    stage, keyed by the TREE. Default: everything `done-gate` requires."""
    where = project / STAGES_DIR / tree
    where.mkdir(parents=True, exist_ok=True)
    for stage in gate_stages("done-gate") if stages is None else stages:
        (where / stage).write_text(f"tree={tree}\nstage={stage}\n", encoding="utf-8")
    return where


# ---------------------------------------------------------------------------
# the shipped binding
# ---------------------------------------------------------------------------


def test_the_maintainer_role_binds_a_gate_to_both_roads_into_master():
    """S4 / the epic's acceptance: a live consumer, bound and proven to refuse.

    TWO rows since HATS-1545 — the app owns the grammar above `run:`, so a row
    belongs to exactly one app. `apps.rack.tasks` is the FSM automerge and
    `apps.wt` is a direct `ai-hats wt merge`; a gate holding only one of them is
    the asymmetry that started the epic, and HATS-1538 left through the unheld
    one.

    ONE script since HATS-1878: the edges still ask different questions
    (ADR-0023 D3/D4), but the question is the ROW's — its `gate:` cargo names
    the set the project's table requires there.
    """
    apps = shipped_apps()
    assert apps["rack"]["tasks"] == [
        {
            "run": f"{SKILL}/{SCRIPT}",
            "gate": "review-gate",
            "at": ["->review"],
            "on_error": "refuse",
        },
        {"run": f"{SKILL}/{SCRIPT}", "gate": "done-gate", "at": ["->done"], "on_error": "refuse"},
    ], "the FSM road: the hand-off and every road into done, qualified by the backlog"
    assert apps["wt"] == [
        {
            "run": f"{SKILL}/{SCRIPT}",
            "gate": "merge-gate",
            "at": ["pre-merge"],
            "on_error": "refuse",
        }
    ], "the direct `ai-hats wt merge` road, gated by ->merge and not by ->done"


def test_the_maintainer_role_is_ai_hats_specific_not_generic():
    """The gate refuses any project with no ``done-gate`` stage, so the role that
    carries it must be one only this codebase wears."""
    text = MAINTAINER_ROLE.read_text(encoding="utf-8")
    assert "/ai-hats-dev/roles/" in MAINTAINER_ROLE.as_posix(), (
        "ai-hats-dev/ is the layer for what only this repository composes"
    )
    assert "ai-hats-maintainer" in text
    assert "ai-hats codebase" in text


# ---------------------------------------------------------------------------
# 1. no marker — the refusal (R1, R5, R6)
# ---------------------------------------------------------------------------


def test_a_branch_with_no_marker_cannot_reach_done(gate_project, rack_bin):
    """R1: the card stays in review, the card file is byte-unchanged, and the
    reason carries the command that clears the gate.

    Consent is supplied (HATS-1682): the shipped row now carries `consent: true`
    as well, and that subscriber is in-lock at priority 11 against `checks`'s 15.
    Without the grant every case below would measure the consent refusal and the
    gate could be deleted with this file still green.
    """
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)
    before = _card(project, task_id).read_bytes()

    refused = _rack(rack_bin, "transition", task_id, "done", cwd=project, env=consented(env))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert _card(project, task_id).read_bytes() == before, "a refused edge must persist nothing"
    assert _state(project, task_id)["state"] == "review"
    assert f"{EDGE} aborted by 'checks'" in refused.stderr
    assert "Traceback" not in refused.stderr, "a refusal must be typed, not a stack"

    as_json = _rack(
        rack_bin, "transition", task_id, "done", "--json", cwd=project, env=consented(env)
    )
    reason = _reason(as_json)
    assert "has not earned every stage" in reason
    # R6: an action, not a diagnosis — the exact command, in the right directory.
    assert f"cd {wt} && make done-gate" in reason
    # ADR-0023 D7: the refusal RENDERS what is missing from the project's own
    # table. A library file that restated it drifted within days.
    assert "Missing:" in reason
    for stage in gate_stages("done-gate"):
        assert stage in reason, f"the refusal names the missing stage {stage}"
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

    blocked = _rack(rack_bin, "transition", task_id, "done", cwd=project, env=consented(env))
    assert blocked.returncode == 1, "positive control: unmarked must refuse first"
    assert f"{EDGE} aborted by 'checks'" in blocked.stderr, (
        "the control must refuse for want of a MARKER, not for want of consent"
    )

    _write_marker(project, _tree(Path(wt)))

    taken = _rack(
        rack_bin, "transition", task_id, "done", "--json", cwd=project, env=consented(env)
    )

    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "done"
    assert "every required stage is green" in _check_log(project, task_id).read_text(
        encoding="utf-8"
    )


def test_a_marker_that_never_ran_a_demanded_stage_does_not_clear_the_gate(gate_project, rack_bin):
    """HATS-1601: add a stage to the gate and every marker on disk kept letting
    transitions through — the check compared the key and nothing else."""
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)
    _write_marker(project, _tree(Path(wt)), stages=gate_stages("done-gate")[:-1])

    refused = _rack(rack_bin, "transition", task_id, "done", cwd=project, env=consented(env))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f"{EDGE} aborted by 'checks'" in refused.stderr, refused.stderr
    assert _state(project, task_id)["state"] == "review"


def test_the_composition_is_read_from_the_tree_under_judgement(gate_project, rack_bin):
    """A card that CHANGES the gate must be judged by the composition it carries.

    Read from the main checkout instead, the gate judges one tree by another
    tree's rules — which is how HATS-1604 refused its own merge: the branch was
    the only place that knew the new contract.
    """
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)
    # The branch grows its gate a stage; master's table never hears of it.
    branch_stage = "a-stage-only-this-branch-declares"
    table = Path(wt) / "scripts" / "gates.sh"
    table.write_text(
        table.read_text(encoding="utf-8").replace(
            "TABLE\n}", f"{branch_stage} | done-gate | only this branch\nTABLE\n}}", 1
        ),
        encoding="utf-8",
    )
    git(Path(wt), "add", "-A")
    git(Path(wt), "commit", "-m", "change what the gate runs")
    _write_marker(project, _tree(Path(wt)))

    refused = _rack(rack_bin, "transition", task_id, "done", cwd=project, env=consented(env))
    assert refused.returncode == 1, "master's set is not enough: the branch's table demands more"
    assert branch_stage in _check_log(project, task_id).read_text(encoding="utf-8")

    _write_marker(project, _tree(Path(wt)), stages=[branch_stage])
    taken = _rack(
        rack_bin, "transition", task_id, "done", "--json", cwd=project, env=consented(env)
    )

    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "done"


# ---------------------------------------------------------------------------
# 3. the key is content, not time — R4
# ---------------------------------------------------------------------------


def test_a_marker_for_a_different_tree_does_not_clear_the_gate(gate_project, rack_bin):
    """R4: a marker earned on earlier content stops applying the moment the tree
    changes — no expiry mechanism exists because none is needed."""
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)

    stale = _tree(Path(wt))
    _write_marker(project, stale)
    # The branch moves on: the marker now describes content that is not the tip.
    (Path(wt) / "more.txt").write_text("second thought", encoding="utf-8")
    git(Path(wt), "add", "-A")
    git(Path(wt), "commit", "-m", "more work")
    tip = _tree(Path(wt))
    assert tip != stale

    refused = _rack(
        rack_bin, "transition", task_id, "done", "--json", cwd=project, env=consented(env)
    )

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

    taken = _rack(
        rack_bin, "transition", task_id, "done", "--json", cwd=project, env=consented(env)
    )

    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "done"
    # Non-vacuous: the gate RAN and said pass, rather than never being reached.
    assert "has no worktree" in _check_log(project, task_id).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4b. ...but a card whose worktree was MERGED still pays — HATS-1664
# ---------------------------------------------------------------------------

WORKTREES_SUB = Path(".agent") / "ai-hats" / "sessions" / "worktrees"


def _merge_by_hand(project: Path, task_id: str, wt: str) -> str:
    """Leave the repo exactly as ``ai-hats wt merge`` does: branch on master, no
    live worktree, a tombstone naming the merge commit. Returns that commit."""
    branch = f"task/{task_id.lower()}"
    git(project, "merge", "--no-ff", "-m", f"Merge branch '{branch}'", branch)
    merge_sha = git(project, "rev-parse", "HEAD").stdout.strip()

    shutil.rmtree(wt, ignore_errors=True)
    key = f"task-{task_id.lower()}"
    (project / WORKTREES_SUB / f"{key}.json").unlink(missing_ok=True)
    tomb_dir = project / WORKTREES_SUB / "merged"
    tomb_dir.mkdir(parents=True, exist_ok=True)
    (tomb_dir / f"{key}.json").write_text(
        json.dumps({"branch": branch, "merge_sha": merge_sha}), encoding="utf-8"
    )
    return merge_sha


def test_a_merged_card_is_judged_by_the_merge_commit_not_waved_through(gate_project, rack_bin):
    """The measured hole: 13 of 17 edge firings answered "has no worktree —
    nothing to gate. Passing", 11 of them on cards already merged into master.

    `wt:pre-merge` fires seconds before the edge and tears the worktree down, so
    the ->done gate saw nothing and never once demanded `merge-smoke`, which its
    own composition names.
    """
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)
    _merge_by_hand(project, task_id, wt)

    refused = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert _state(project, task_id)["state"] == "review"
    assert "has no worktree" not in _check_log(project, task_id).read_text(encoding="utf-8")


def test_a_marker_for_the_merge_commit_lets_the_merged_card_through(gate_project, rack_bin):
    """The other half: the gate names a tree that CAN be earned, and earning it
    clears the edge. Otherwise the fix above would be an unopenable door."""
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)
    merge_sha = _merge_by_hand(project, task_id, wt)

    blocked = _rack(rack_bin, "transition", task_id, "done", cwd=project, env=env)
    assert blocked.returncode == 1, "positive control: unmarked must refuse first"

    _write_marker(project, _tree(project, merge_sha))

    taken = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)

    assert taken.returncode == 0, taken.stdout + taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "done"


def test_the_refusal_of_a_merged_card_hands_over_a_command_that_can_earn_it(gate_project, rack_bin):
    """A refusal is an ACTION, not a diagnosis (ADR-0023 D6) — and the plain
    `make done-gate` it used to hand over could not possibly work here. The
    card's worktree is gone, so the agent stands in the main checkout: HEAD has
    moved under other merges and the tree is dirty, so that run would judge the
    wrong content and then decline to record it. `REV=` names the subject."""
    project, env = gate_project("gated")
    task_id, wt = _to_review(rack_bin, project, env, worktree=True)
    merge_sha = _merge_by_hand(project, task_id, wt)

    refused = _rack(rack_bin, "transition", task_id, "done", cwd=project, env=env)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f"REV={merge_sha}" in _check_log(project, task_id).read_text(encoding="utf-8")


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
    assert not _checks_dir(project, task_id).exists(), "an unbound role must run no check"


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
    assert "apps:" not in role.read_text(encoding="utf-8"), "the rows must really be gone"

    taken = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=env)

    assert taken.returncode == 0, (
        "with the checks row removed the unmarked branch MUST reach done — "
        f"if it still refuses, case 1 was never proving the gate\n{taken.stdout}{taken.stderr}"
    )
    assert json.loads(taken.stdout)["task"]["state"] == "done"
    assert not _checks_dir(project, task_id).exists()


# ---------------------------------------------------------------------------
# 7. the OTHER road into master — a direct `ai-hats wt merge` (HATS-1540)
# ---------------------------------------------------------------------------


def _ai_hats(launcher: Path, *args: str, cwd: Path, env: dict[str, str]):
    return subprocess.run(  # noqa: S603 - launcher from the shared-launcher fixture
        [str(launcher), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_a_direct_wt_merge_is_refused_before_it_mutates_anything(
    gate_project, rack_bin, shared_launcher
):
    """The door HATS-1538 left through, now held.

    ``ai-hats wt merge`` is the second road into master and had no precondition
    at all — the incident that opened this epic (HATS-1130) and the way HATS-1538
    itself escaped the stuck card it had created. Everything the merge would
    touch is asserted untouched: no merge commit on master, the worktree still on
    disk, the branch still there, the card still in ``review``.
    """
    launcher, _base_env, _venv = shared_launcher
    project, env = gate_project("gated")
    task_id, worktree = _to_review(rack_bin, project, env, worktree=True)
    branch = f"task/{task_id.lower()}"
    master_before = git(project, "rev-parse", "master").stdout.strip()

    merged = _ai_hats(
        launcher, "wt", "merge", branch, cwd=project, env={**env, "AI_HATS_MERGE_ACK": "1"}
    )

    said = merged.stdout + merged.stderr
    assert merged.returncode != 0, f"the direct merge road is unguarded\n{said}"
    assert "checks" in said.lower(), f"the refusal must name the subsystem\n{said}"
    # `merge-gate`, not `done-gate`: this road is about entering master, and
    # handing the agent the wrong command costs it the ~12s of `merge-smoke`
    # plus a second look at which gate actually refused (HATS-1614).
    assert "make merge-gate" in said, f"the refusal must carry the command that clears it\n{said}"
    assert git(project, "rev-parse", "master").stdout.strip() == master_before, "master moved"
    assert Path(worktree).is_dir(), "a refused pre-merge destroyed the worktree"
    assert branch in git(project, "branch", "--list", branch).stdout
    assert _state(project, task_id)["state"] == "review"


def test_a_done_gate_run_clears_the_merge_gate_on_the_same_tree(
    gate_project, rack_bin, shared_launcher
):
    """Absorption on the live road (ADR-0023 D5).

    The markers planted are `done-gate`'s set and the road being walked is
    guarded by `merge-gate` — a different gate. It clears because every stage
    it demands is marked for this exact tree, which is the whole point: a card
    that ran the fuller gate does not pay twice.
    """
    launcher, _base_env, _venv = shared_launcher
    project, env = gate_project("gated")
    task_id, worktree = _to_review(rack_bin, project, env, worktree=True)
    branch = f"task/{task_id.lower()}"
    _write_marker(project, _tree(Path(worktree)))
    assert set(gate_stages("done-gate")) > set(gate_stages("merge-gate")), (
        "the point is that a WIDER gate's markers were written"
    )

    merged = _ai_hats(
        launcher, "wt", "merge", branch, cwd=project, env={**env, "AI_HATS_MERGE_ACK": "1"}
    )

    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert not Path(worktree).exists(), "a merged worktree is torn down"


def test_removing_the_checks_row_lets_the_direct_merge_through(
    gate_project, rack_bin, shared_launcher
):
    """Fail-under-revert for the second road — both outcomes asserted, not two
    green runs. Same sandbox, same unmarked branch, only the row is gone."""
    launcher, _base_env, _venv = shared_launcher
    project, env = gate_project("gated", bind=False)
    task_id, worktree = _to_review(rack_bin, project, env, worktree=True)
    branch = f"task/{task_id.lower()}"

    merged = _ai_hats(
        launcher, "wt", "merge", branch, cwd=project, env={**env, "AI_HATS_MERGE_ACK": "1"}
    )

    assert merged.returncode == 0, (
        "with the checks row removed the unmarked branch MUST merge — if it "
        f"still refuses, the case above was never proving the gate\n"
        f"{merged.stdout}{merged.stderr}"
    )
    assert not Path(worktree).exists()


# ---------------------------------------------------------------------------
# 8. someone else's backlog — the case that turned master red (HATS-1538)
# ---------------------------------------------------------------------------


def test_a_card_in_a_foreign_backlog_is_not_this_gates_business(
    gate_project, rack_bin, tmp_path: Path
):
    """G: the measured leak, closed at its source.

    The shipped row fired on EVERY backlog the rack CLI touched — including the
    scratch ``--tasks-dir`` this repo's own rack tests build — and refused them
    by its own contract, which is what turned master red. Ruling 2026-08-08 P1
    answered that by keeping the binding role-scoped and making the SCRIPT
    declare "not mine", because the engine had no notion of whose backlog it
    was. HATS-1573 gave it one, and the ruling of 2026-08-11 moved the scope to
    the backlog: a backlog nobody owns has no gates, so nothing fires at all.

    Since HATS-1682 that covers the CONSENT half of the same row too — no grant
    is made below, and the edge must still be taken.
    """
    project, env = gate_project("gated")
    scratch = tmp_path / "scratch-backlog" / "tasks"
    scratch.mkdir(parents=True)
    foreign = {**env, "RACK_TASKS_DIR": str(scratch)}

    created = _rack(rack_bin, "create", "someone else's card", cwd=project, env=foreign)
    assert created.returncode == 0, created.stdout + created.stderr
    task_id = re.search(r"Created: (\S+)", created.stdout).group(1)
    forced = _rack(
        rack_bin,
        "transition",
        task_id,
        "review",
        "--force",
        "--reason",
        "straight to review",
        cwd=project,
        env=foreign,
    )
    assert forced.returncode == 0, forced.stdout + forced.stderr

    taken = _rack(rack_bin, "transition", task_id, "done", "--json", cwd=project, env=foreign)

    assert taken.returncode == 0, (
        "a card outside this project's own tracker must not be refused by this "
        f"project's gate — that is the HATS-1538 regression\n{taken.stdout}{taken.stderr}"
    )
    assert json.loads(taken.stdout)["task"]["state"] == "done"
    # Nothing ran, so there is no log to read: the scratch backlog belongs to no
    # project, and only a backlog's own project can bind a check to it.
    assert not (scratch / task_id / ".checks").exists()
    # And the absence is announced — a gate that is not there must not be
    # mistaken for a gate that passed.
    assert "no project owns" in taken.stderr, taken.stderr


def test_done_gate_runs_e2e_catalog_first_and_refuses_stale_catalog(tmp_path: Path):
    """HATS-1562/HATS-1604: the composition names `e2e-catalog` first and the
    primitive stops at the first red, so a stale CATALOG.md refuses before the
    expensive stages (lint, unit, integration) are ever started.

    HATS-1716: the red stage is a STUB dispatcher's, not the live tree's. This
    test used to corrupt `tests/e2e/CATALOG.md` in the checkout it runs from and
    restore it in a `finally` — a write every sibling session and all 8 xdist
    workers could observe, and the one HATS-1714 spent a card chasing.
    """
    real = gate_stages("done-gate")
    assert real[0] == "e2e-catalog", f"e2e-catalog must lead the composition: {real}"
    assert "lint" in real[1:], f"lint must follow it, or this test proves nothing: {real}"

    # A clean sandbox with the real table and primitive, and a runner whose
    # `e2e-catalog` is red: the run must stop there, before `lint`.
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    init_repo(sandbox, branch="master")
    (sandbox / "scripts").mkdir()
    for name in PROJECT_SCRIPTS:
        shutil.copy(REPO_ROOT / "scripts" / name, sandbox / "scripts" / name)
    runner = sandbox / "scripts" / "ci-local.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        'echo "[ci-local] $1" >&2\n'
        '[[ "$1" == "e2e-catalog" ]] && exit 1\n'
        "exit 0\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    git(sandbox, "add", "-A")
    git(sandbox, "commit", "-m", "seed")

    proc = subprocess.run(
        ["bash", str(sandbox / "scripts" / "gates.sh"), "run", "done-gate"],
        cwd=str(sandbox),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0, (
        f"a red e2e-catalog must fail done-gate:\n{proc.stdout}\n{proc.stderr}"
    )
    combined = proc.stdout + proc.stderr
    assert "[ci-local] e2e-catalog" in combined, (
        f"done-gate output must announce e2e-catalog stage:\n{combined}"
    )
    assert "[ci-local] lint" not in combined, (
        f"done-gate must fail at e2e-catalog stage BEFORE reaching lint:\n{combined}"
    )
