"""e2e (HATS-1604, HATS-1878)

flow:   a thin gate script on the checks channel, judging the tree a card puts into master
cmds:
    AI_HATS_WORKTREE_PATH=<wt> hooks/done-gate.sh
    hooks/done-gate.sh --stages
    bash -c '. lib/gate.sh; gate_exit checks refuse'
expect: a card with no code passes; a live worktree is judged by ITS scripts/gates.sh
        against the stages the gate declares, and refused with the missing ones
        and the command that earns them; a merged card is judged by its merge commit;
        a green run names what the gates after it still lack
why:    a gate used to be sixty lines that differed from its siblings in two
        strings; a few-line declaration over one primitive cannot drift
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.git import commit_file, git, init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_SRC = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library"
    / "ai-hats-dev"
    / "skills"
    / "maintainer-quality-gate"
)
HOOKS = SKILL_SRC / "hooks"
GATE_LIB = SKILL_SRC / "lib" / "gate.sh"

#: A stage runner that runs nothing and answers nothing: a check must never
#: reach it (it runs inside the rack lock), and `gates.sh check` has no code
#: path to it.
RUNNER_STUB = '#!/usr/bin/env bash\ntouch "$(dirname "$0")/runner-ran"\nexit 99\n'


def _project(root: Path) -> Path:
    """A clean main checkout carrying the project's side of the gate — the real
    `scripts/gates.sh` — and an `ai-hats.yaml` so the backlog-scope question can
    be answered. Stages, if anything ran them, would hit the stub."""
    root.mkdir(parents=True)
    init_repo(root, branch="master")
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "gates.sh", scripts / "gates.sh")
    (root / "ai-hats.yaml").write_text("ai_hats_dir: .agent/ai-hats\n")
    (root / ".gitignore").write_text(".agent/\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "seed")
    return root


def _worktree(project: Path, name: str) -> Path:
    wt = project.parent / f"wt-{name}"
    git(project, "worktree", "add", "-q", "-b", f"task/{name}", str(wt))
    commit_file(wt, "work.txt", "work\n", "work")
    return wt


PUSH_HOOK = SKILL_SRC / "git_hooks" / "pre-push-e2e-master.sh"


def _stages(gate: str) -> list[str]:
    out = subprocess.run(
        ["bash", str(HOOKS / f"{gate}.sh"), "--stages"], capture_output=True, text=True, check=True
    )
    return out.stdout.split()


def _push_stages() -> list[str]:
    out = subprocess.run(
        ["bash", str(PUSH_HOOK), "--stages"], capture_output=True, text=True, check=True
    )
    return out.stdout.split()


def _mark(project: Path, tree: str, stages: list[str]) -> None:
    where = project / ".git" / "ai-hats" / "stages" / tree
    where.mkdir(parents=True, exist_ok=True)
    for stage in stages:
        (where / stage).write_text(f"tree={tree}\nstage={stage}\n")


def _tree(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "rev-parse", f"{rev}^{{tree}}").stdout.strip()


def _hook(
    project: Path, gate: str, env: dict[str, str | None], *argv: str
) -> subprocess.CompletedProcess[str]:
    """Spawn a gate the way the check runner does: bare, from the project dir,
    with the shared env base plus the caller's own vocabulary."""
    child = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    child["AI_HATS_PROJECT_DIR"] = str(project)
    child["AI_HATS_IN_HOOK"] = "1"
    child["AI_HATS_TASK_ID"] = "SBX-1"
    child["AI_HATS_TASKS_DIR"] = str(
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    )
    child["GATES_STAGE_RUNNER"] = str(project.parent / "runner-stub.sh")
    for name, value in env.items():
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value
    stub = project.parent / "runner-stub.sh"
    if not stub.exists():
        stub.write_text(RUNNER_STUB)
        stub.chmod(0o755)
    return subprocess.run(
        ["bash", str(HOOKS / f"{gate}.sh"), *argv],
        cwd=project,
        capture_output=True,
        text=True,
        env=child,
        check=False,
    )


def _push_hook(
    project: Path, env: dict[str, str | None], *argv: str
) -> subprocess.CompletedProcess[str]:
    """The push gate's own script — a separate file, because its default mode
    reads git's protocol from stdin. Only `--run` is reachable from here."""
    child = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    child["GATES_STAGE_RUNNER"] = str(project.parent / "runner-stub.sh")
    for name, value in env.items():
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value
    return subprocess.run(
        ["bash", str(PUSH_HOOK), *argv],
        cwd=project,
        capture_output=True,
        text=True,
        env=child,
        check=False,
    )


# ---------------------------------------------------------------------------
# a gate is a declaration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gate", ["review-gate", "merge-gate", "done-gate"])
def test_every_gate_declares_its_stages(gate: str):
    """HATS-1878: the gate IS its stage list; `--stages` is how a reader, a test
    and the ADR renderer learn it without parsing the file."""
    assert _stages(gate), f"{gate} declares no stage"


def test_a_typo_at_the_command_line_is_not_a_verdict(tmp_path: Path):
    project = _project(tmp_path / "proj")

    out = _hook(project, "done-gate", {}, "--frobnicate")

    assert out.returncode == 64


# ---------------------------------------------------------------------------
# the subject — what this card puts into master
# ---------------------------------------------------------------------------


def test_a_card_with_no_worktree_and_nothing_merged_passes(tmp_path: Path):
    """A doc or research card brings no commits, so it pays for none."""
    project = _project(tmp_path / "proj")

    out = _hook(project, "done-gate", {"AI_HATS_WORKTREE_PATH": None, "AI_HATS_MERGED_SHA": None})

    assert out.returncode == 0, out.stdout + out.stderr
    assert "has no worktree" in out.stdout


def test_a_live_worktree_without_markers_is_refused_with_the_missing_stages(tmp_path: Path):
    """The refusal is an action: every missing stage, and `cd <wt> && make <gate>`."""
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")

    out = _hook(project, "review-gate", {"AI_HATS_WORKTREE_PATH": str(wt)})

    assert out.returncode == 2, out.stdout + out.stderr
    assert "Missing:" in out.stdout
    for stage in _stages("review-gate"):
        assert f"    {stage}\n" in out.stdout
    assert f"cd {wt} && make review-gate" in out.stdout
    assert _tree(wt) in out.stdout, "the refusal names the tree it wanted"
    # Gate, tree, Missing, command: the standing paragraph that used to follow
    # the command explained the marker model to a reader who wanted a verb.
    assert "Run the gate" in out.stdout, "the command keeps its one-line intro"
    assert "It runs only what is missing" not in out.stdout
    assert out.stdout.rstrip().splitlines()[-1].strip() == f"cd {wt} && make review-gate"


def test_markers_for_every_required_stage_let_the_worktree_through(tmp_path: Path):
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")
    _mark(project, _tree(wt), _stages("review-gate"))

    out = _hook(project, "review-gate", {"AI_HATS_WORKTREE_PATH": str(wt)})

    assert out.returncode == 0, out.stdout + out.stderr
    assert "every required stage is green" in out.stdout


def test_a_wider_gates_markers_cover_a_narrower_gate_on_the_same_tree(tmp_path: Path):
    """Absorption (ADR-0023 D5) is set inclusion over per-stage markers: a
    `done-gate` run stamps everything `review-gate` and `merge-gate` ask for."""
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")
    _mark(project, _tree(wt), _stages("done-gate"))

    for gate in ("review-gate", "merge-gate"):
        out = _hook(project, gate, {"AI_HATS_WORKTREE_PATH": str(wt)})
        assert out.returncode == 0, (gate, out.stdout + out.stderr)


def test_the_narrower_gates_markers_do_not_cover_the_wider_one(tmp_path: Path):
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")
    _mark(project, _tree(wt), _stages("review-gate"))

    out = _hook(project, "done-gate", {"AI_HATS_WORKTREE_PATH": str(wt)})

    assert out.returncode == 2
    only_done = set(_stages("done-gate")) - set(_stages("review-gate"))
    missing = out.stdout.split("Missing:", 1)[1].split("Run the gate", 1)[0].split()
    assert set(missing) == only_done, "exactly the stages the wider gate adds"


def test_a_merged_card_is_judged_by_its_merge_commit(tmp_path: Path):
    """No worktree AND a merge behind it: the subject is the merge commit's tree,
    and the command handed over names it with REV=."""
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")
    git(project, "merge", "--no-ff", "-q", "-m", "merge", "task/one")
    merged = git(project, "rev-parse", "HEAD").stdout.strip()
    git(project, "worktree", "remove", "--force", str(wt))
    env = {"AI_HATS_WORKTREE_PATH": str(wt), "AI_HATS_MERGED_SHA": merged}

    refused = _hook(project, "done-gate", env)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert f"merge commit {merged}" in refused.stdout
    assert f"make done-gate REV={merged}" in refused.stdout

    _mark(project, _tree(project, merged), _stages("done-gate"))
    passed = _hook(project, "done-gate", env)
    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_a_swept_worktree_with_nothing_merged_passes(tmp_path: Path):
    """The record survived its worktree and nothing reached master: rack's own
    teardown refuses an unmerged branch, so there is nothing here to gate."""
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")
    git(project, "worktree", "remove", "--force", str(wt))

    out = _hook(
        project, "done-gate", {"AI_HATS_WORKTREE_PATH": str(wt), "AI_HATS_MERGED_SHA": None}
    )

    assert out.returncode == 0, out.stdout + out.stderr
    assert "no longer exists" in out.stdout


def test_a_card_in_a_foreign_backlog_is_not_this_gates_business(tmp_path: Path):
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")

    out = _hook(
        project,
        "done-gate",
        {
            "AI_HATS_WORKTREE_PATH": str(wt),
            "AI_HATS_TASKS_DIR": str(tmp_path / "elsewhere" / "tasks"),
        },
    )

    assert out.returncode == 0, out.stdout + out.stderr
    assert "not this project's backlog" in out.stdout


def test_a_project_without_gates_sh_cannot_pass(tmp_path: Path):
    """The library never runs a stage itself (D7): a tree with no `scripts/gates.sh`
    cannot earn a marker, so it cannot pass."""
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")
    (wt / "scripts" / "gates.sh").unlink()
    git(wt, "commit", "-qam", "drop the runner")

    out = _hook(project, "done-gate", {"AI_HATS_WORKTREE_PATH": str(wt)})

    assert out.returncode == 2, out.stdout + out.stderr
    assert "no scripts/gates.sh" in out.stdout


def test_the_check_never_reaches_the_stage_runner(tmp_path: Path):
    """The runner stub leaves a file when invoked; a check that ran anything
    would leave it. (The old probe, "99" absent from the output, tripped on a
    tree hash that happened to contain it.)"""
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")

    out = _hook(project, "done-gate", {"AI_HATS_WORKTREE_PATH": str(wt)})

    assert out.returncode == 2
    assert not (project.parent / "runner-ran").exists(), "the check reached the stage runner"


# ---------------------------------------------------------------------------
# --run: earn the stages, and on red say how to resume
# ---------------------------------------------------------------------------


def test_a_red_run_names_the_command_that_resumes_it_right_after_the_verdict(tmp_path: Path):
    """`gates.sh` prints its block verdict first and the resume command second;
    it cannot spell that command, so the gate hands it down. The stub runner
    exits 99, so the run is red at its first stage."""
    project = _project(tmp_path / "proj")
    wt = _worktree(project, "one")

    in_place = _hook(project, "done-gate", {}, "--run")
    assert in_place.returncode == 99, in_place.stderr
    lines = in_place.stderr.splitlines()
    assert lines[0].startswith("[gates] RESULT "), "the primitive's verdict opens the block"
    assert lines[1] == "[gates] fix e2e-catalog, then: make done-gate"

    sha = git(wt, "rev-parse", "HEAD").stdout.strip()
    of_a_commit = _hook(project, "done-gate", {}, "--run", "--rev", sha)
    assert of_a_commit.returncode == 99, of_a_commit.stderr
    assert of_a_commit.stderr.splitlines()[1] == (
        f"[gates] fix e2e-catalog, then: make done-gate REV={sha}"
    )


def _green_runner(project: Path) -> Path:
    """A stage runner that passes everything, so a run reaches its own end."""
    script = project.parent / "green-runner.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    return script


def _next_line(res: subprocess.CompletedProcess[str]) -> str:
    for line in res.stderr.splitlines():
        if line.startswith("[gates] next: "):
            return line[len("[gates] next: ") :]
    return ""


def test_a_green_run_names_what_the_next_gate_will_additionally_require(tmp_path: Path):
    """The two round trips this kills: a green run, then a transition refused
    for stages the run could already see were missing. Read from the markers,
    so a gate whose set this run just earned — `merge-gate`, which declares
    review's list verbatim — is not named, and neither is the gate that ran."""
    project = _project(tmp_path / "proj")
    env = {"GATES_STAGE_RUNNER": str(_green_runner(project))}

    out = _hook(project, "review-gate", env, "--run")

    assert out.returncode == 0, out.stderr
    line = _next_line(out)
    assert line, out.stderr
    assert "review-gate" not in line, "it just went green; its own stages are marked"
    assert "merge-gate" not in line, "same list as review's, so nothing is left of it"
    earned = set(_stages("review-gate"))
    for gate, declared in (("done-gate", _stages("done-gate")), ("push-gate", _push_stages())):
        # In the gate's own order, which is the order the stages would run.
        only_it = [stage for stage in declared if stage not in earned]
        assert f"{gate} also needs {', '.join(only_it)}" in line


def test_gates_owing_the_same_thing_are_named_together(tmp_path: Path):
    """The measured sequence: a green push gate, then `->review` refused. The
    push gate's list covers neither `review-gate` nor `merge-gate`, which
    declare one list between them — so they are one entry, with the verb that
    follows from the count."""
    project = _project(tmp_path / "proj")
    env = {"GATES_STAGE_RUNNER": str(_green_runner(project))}

    out = _push_hook(project, env, "--run")

    assert out.returncode == 0, out.stderr
    pushed = set(_push_stages())
    owed = ", ".join(stage for stage in _stages("review-gate") if stage not in pushed)
    assert f"merge-gate, review-gate also need {owed}" in _next_line(out)
    assert "push-gate" not in _next_line(out), "the gate that ran owes nothing now"


def test_a_tree_that_owes_nothing_anywhere_says_so(tmp_path: Path):
    """The end of the road is worth one line too: nothing left means the next
    transition will not refuse, which is the last round trip this saves."""
    project = _project(tmp_path / "proj")
    env = {"GATES_STAGE_RUNNER": str(_green_runner(project))}
    every = {
        stage for gate in ("review-gate", "merge-gate", "done-gate") for stage in _stages(gate)
    }
    _mark(project, _tree(project), sorted(every | set(_push_stages())))

    out = _hook(project, "done-gate", env, "--run")

    assert out.returncode == 0, out.stderr
    assert _next_line(out) == "nothing — every gate is green for this tree"


def test_a_red_run_says_nothing_about_the_next_gate(tmp_path: Path):
    """One job at a time: a red run's reader is fixing this gate, not planning
    the one after it. The stub runner exits 99 at the first stage."""
    project = _project(tmp_path / "proj")

    out = _hook(project, "review-gate", {}, "--run")

    assert out.returncode == 99, out.stderr
    assert _next_line(out) == ""


# ---------------------------------------------------------------------------
# one verdict, two channels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "outcome", "rc"),
    [
        ("checks", "pass", 0),
        ("checks", "refuse", 2),
        ("githook", "pass", 0),
        ("githook", "refuse", 1),
    ],
)
def test_one_outcome_maps_onto_each_channels_own_exit_contract(
    tmp_path: Path, channel: str, outcome: str, rc: int
):
    ran = subprocess.run(
        ["bash", "-c", f'set -uo pipefail; . "{GATE_LIB}"; gate_exit {channel} {outcome}'],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert ran.returncode == rc, ran.stderr
