"""e2e (HATS-1604/HATS-1601)

flow:   a gate script asking whether this tree already earned a marker
cmds:
    bash -c '. lib/gate-marker.sh; gate_marker_write done-gate . <tree> ...'
    bash -c '. lib/gate.sh; gate_exit checks refuse'
expect: the marker keys on the TREE, carries the composition it certifies, and
        one primitive maps an outcome onto each channel's exit codes
why:    the discipline was hand-written twice with a diverging exit contract,
        and a commit-keyed marker made every --no-ff merge pay twice
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.git import commit_file, git, init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_SRC = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library"
    / "usage"
    / "skills"
    / "maintainer-quality-gate"
)
MARKER_LIB = SKILL_SRC / "lib" / "gate-marker.sh"
GATE_LIB = SKILL_SRC / "lib" / "gate.sh"


def _bash(script: str, cwd: Path, lib: Path = MARKER_LIB) -> subprocess.CompletedProcess[str]:
    """Source one library and run `script` under a real bash."""
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail; . "{lib}"\n{script}'],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    init_repo(project)
    return project


def _tree(repo_dir: Path, rev: str = "HEAD") -> str:
    return git(repo_dir, "rev-parse", f"{rev}^{{tree}}").stdout.strip()


# ---------------------------------------------------------------------------
# 1. the marker carries the composition it certifies (HATS-1601)
# ---------------------------------------------------------------------------


def test_a_marker_does_not_certify_a_stage_it_never_ran(repo: Path):
    """Add a stage to a gate and every marker on disk keeps letting transitions
    through: `gate_marker_ok` compared `sha=` and nothing else, so SKILL.md's
    "a marker cannot go stale" was true by content and false by composition."""
    tree = _tree(repo)
    written = _bash(f'gate_marker_write done-gate . "{tree}" lint', repo)
    assert written.returncode == 0, written.stdout + written.stderr

    ok = _bash(f'gate_marker_ok . "{tree}" lint unit', repo)

    assert ok.returncode != 0, (
        "the marker certifies `lint` alone — a gate demanding `lint unit` must not accept it"
    )


def _dispatcher(repo: Path, *, red: str = "") -> Path:
    """A stand-in for scripts/ci-local.sh: it answers --stages and runs stages."""
    path = repo / "dispatch.sh"
    path.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--stages" ]]; then echo "one two three"; exit 0; fi\n'
        'touch "$(dirname "$0")/ran-$1"\n'
        f'[[ "$1" == "{red}" ]] && exit 3\n'
        "exit 0\n",
        encoding="utf-8",
    )
    return path


def test_asking_a_dispatcher_that_predates_the_flag_runs_nothing(repo: Path):
    """MEASURED (HATS-1604): asked as `<gate> --stages`, a pre-1604 dispatcher
    read the flag as a trailing argument to a gate it DID know and ran the whole
    thing — a 2-minute hang inside a 20s in-lock budget. `--stages` goes first,
    where an unknown flag is just an unknown stage and refuses in milliseconds."""
    old = repo / "old-dispatch.sh"
    old.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "done-gate" ]]; then touch "$(dirname "$0")/RAN_THE_GATE"; exit 0; fi\n'
        'echo "unknown stage: $1" >&2\nexit 2\n',
        encoding="utf-8",
    )

    asked = _bash(f'gate_stages "{old}" done-gate', repo, lib=GATE_LIB)

    assert asked.returncode != 0, "a dispatcher that names no composition must answer no"
    assert not (repo / "RAN_THE_GATE").exists(), "asking what a gate IS must never RUN it"


def test_the_run_stops_at_the_first_red_stage(repo: Path):
    """The expensive half must not start after a cheap stage already said no."""
    dispatcher = _dispatcher(repo, red="two")

    ran = _bash(f'gate_run "{dispatcher}" done-gate', repo, lib=GATE_LIB)

    assert ran.returncode == 3, "the gate's rc is the failing stage's own rc"
    assert (repo / "ran-two").exists(), "the red stage ran"
    assert not (repo / "ran-three").exists(), "nothing after it did"


def test_a_green_run_on_a_dirty_tree_earns_no_marker(repo: Path):
    """A marker describes COMMITTED content, so on a dirty tree what passed is
    not what the branch holds. The run still counts as a run — rc 0."""
    (repo / "uncommitted.txt").write_text("dirty", encoding="utf-8")
    tree = _tree(repo)

    stamped = _bash(
        f'. "{MARKER_LIB}"; gate_stamp done-gate . "{tree}" "lint unit"', repo, lib=GATE_LIB
    )

    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    assert "dirty" in stamped.stderr
    ok = _bash(f'gate_marker_ok . "{tree}" lint', repo)
    assert ok.returncode != 0, "no marker may exist for a tree that was never committed"


@pytest.mark.parametrize(
    ("channel", "outcome", "code"),
    [
        ("githook", "pass", 0),
        ("githook", "refuse", 1),
        ("checks", "pass", 0),
        ("checks", "refuse", 2),
    ],
)
def test_one_outcome_maps_onto_each_channel_s_own_exit_contract(
    repo: Path, channel: str, outcome: str, code: int
):
    """The reason the primitive exists (ADR-0023 D6): the SAME verdict is spelled
    0/1 to git and 0/2/126/127 to the checks channel (ADR-0020 D2), and both
    mappings were written by hand, once per script."""
    ran = _bash(f"gate_exit {channel} {outcome}", repo, lib=GATE_LIB)

    assert ran.returncode == code, ran.stdout + ran.stderr


def test_a_marker_covers_a_gate_whose_composition_it_includes(repo: Path):
    """The absorption rule (ADR-0023 D5): a fuller run stamps every gate whose
    composition it contains, so a typical card costs one run, not two."""
    tree = _tree(repo)
    _bash(f'gate_marker_write done-gate . "{tree}" "lint unit integration"', repo)

    ok = _bash(f'gate_marker_ok . "{tree}" lint unit', repo)

    assert ok.returncode == 0, "required ⊆ recorded — the run already covered this gate"


def test_a_run_of_one_gate_covers_another_gate_on_the_same_tree(repo: Path):
    """Absorption ACROSS gate names (HATS-1614) — the half D5 promised and the
    marker store did not deliver. Markers were read out of one directory per
    gate, so `done-gate` running a superset of `merge-gate` on the same tree left
    `merge-gate` refusing, and the card paid for both."""
    tree = _tree(repo)
    _bash(f'gate_marker_write done-gate . "{tree}" "lint unit integration merge-smoke"', repo)

    ok = _bash(f'gate_marker_ok . "{tree}" lint unit integration', repo)

    assert ok.returncode == 0, (
        "`merge-gate`'s stages all ran on this tree under `done-gate` — "
        "which directory recorded them is not the question a gate asks"
    )


def test_stages_no_gate_ever_ran_are_not_conjured_by_the_union(repo: Path):
    """The guard against the union degenerating into "some marker exists". Two
    honest markers for one tree, neither carrying `integration`: the sum of what
    ran is still not what this gate demands."""
    tree = _tree(repo)
    _bash(f'gate_marker_write merge-gate . "{tree}" "lint unit"', repo)
    _bash(f'gate_marker_write e2e-gate . "{tree}" "lint e2e-catalog"', repo)

    ok = _bash(f'gate_marker_ok . "{tree}" lint unit integration', repo)

    assert ok.returncode != 0, "`integration` ran under no gate — the union must not invent it"


# ---------------------------------------------------------------------------
# 2. the run mode that judges a COMMIT, not "here" (HATS-1664)
# ---------------------------------------------------------------------------


def _commit_dispatcher(repo: Path, stages: str, msg: str) -> None:
    """Commit a stand-in `scripts/ci-local.sh` — the path the gate resolves. It
    answers `--stages`, greets `--prepare`, and passes every stage."""
    commit_file(
        repo,
        "scripts/ci-local.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--stages" ]]; then echo "' + stages + '"; exit 0; fi\n'
        'if [[ "$1" == "--prepare" ]]; then echo "PREPARED $PWD"; exit 0; fi\n'
        'echo "ran $1 in $PWD with PYTHON=[${PYTHON:-<unset>}]"\nexit 0\n',
        msg,
    )


def _repo_with_a_merge_left_behind(repo: Path) -> str:
    """The shape every card is in at `review--done`: its content reached master
    as a merge commit, its worktree is gone, master has moved on since, and the
    main checkout carries untracked work. Returns that merge commit."""
    _commit_dispatcher(repo, "one two", "dispatcher at the merge")
    git(repo, "checkout", "-q", "-b", "task/hats-999")
    commit_file(repo, "shipped.txt", "what the card contributed", "the card's own commit")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "-q", "-m", "Merge branch 'task/hats-999'", "task/hats-999")
    merge_sha = git(repo, "rev-parse", "HEAD").stdout.strip()

    commit_file(repo, "someone-else.txt", "another card, merged after ours", "master moves on")
    (repo / "untracked.txt").write_text("the supervisor's desk is never clean", encoding="utf-8")
    return merge_sha


def test_a_run_at_a_rev_marks_that_commit_and_not_where_the_agent_stands(repo: Path):
    """MEASURED HOLE (HATS-1664). The `->done` gate demands a marker for the tree
    of the merge commit, and the only run mode that existed judged `HEAD` of
    wherever it was invoked. By that edge the card's worktree is gone, so the
    agent stands in the main checkout — whose HEAD has moved under other merges
    and whose tree is dirty, which `gate_stamp` rightly refuses to certify.
    Green run, no marker, refusal forever: a door with no key.
    """
    merge_sha = _repo_with_a_merge_left_behind(repo)

    ran = _bash(
        f'. "{MARKER_LIB}"; gate_run_and_stamp_rev done-gate "{merge_sha}" "next"',
        repo,
        lib=GATE_LIB,
    )

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "PREPARED" in ran.stdout + ran.stderr, "the project got to make the checkout runnable"
    earned = _bash(f'gate_marker_ok . "{_tree(repo, merge_sha)}" one two', repo)
    assert earned.returncode == 0, "the commit this card is accountable for is marked"
    here = _bash(f'gate_marker_ok . "{_tree(repo)}" one two', repo)
    assert here.returncode != 0, "and nothing else is — HEAD was never the subject"


def test_every_stage_runs_inside_the_checkout_and_not_where_it_was_called_from(repo: Path):
    """MEASURED on the rev road's first real run (HATS-1664). Naming the
    dispatcher by path is not enough: a dispatcher re-derives its own root with
    `git rev-parse --show-toplevel` from the CWD IT INHERITS, so the right file
    ran the right stages against the caller's tree — and reported that as the
    verdict on the commit. The gate's own log said "judging commit <sha>" while
    the venv it prepared belonged to somewhere else entirely.
    """
    merge_sha = _repo_with_a_merge_left_behind(repo)

    ran = _bash(
        f'. "{MARKER_LIB}"; gate_run_and_stamp_rev done-gate "{merge_sha}" "next"',
        repo,
        lib=GATE_LIB,
    )

    said = ran.stdout + ran.stderr
    assert ran.returncode == 0, said
    for line in ("PREPARED ", "ran one in ", "ran two in "):
        where = said.split(line, 1)[1].splitlines()[0].strip()
        assert "ai-hats-gate-" in where, f"{line.strip()} happened in {where}, not in the checkout"


def test_an_interpreter_from_another_checkout_does_not_ride_along(repo: Path):
    """The other half of the same substitution, measured on the same run
    (HATS-1664): the entry point hands the dispatcher a `PYTHON` naming the
    CALLER's interpreter, whose editable install points at the caller's source.
    It beats the venv this road just built, so the stages import the wrong code
    — caught only by the HATS-1242 guard, and only after paying for the venv."""
    merge_sha = _repo_with_a_merge_left_behind(repo)

    ran = _bash(
        f"export PYTHON=/somewhere/else/.venv/bin/python\n"
        f'. "{MARKER_LIB}"; gate_run_and_stamp_rev done-gate "{merge_sha}" "next"',
        repo,
        lib=GATE_LIB,
    )

    said = ran.stdout + ran.stderr
    assert ran.returncode == 0, said
    assert "PYTHON=[<unset>]" in said, "the stages must resolve the checkout's own interpreter"
    assert "/somewhere/else" in said, (
        "and the gate must say what it dropped, not drop it in silence"
    )


def test_the_scratch_checkout_does_not_outlive_the_run(repo: Path):
    """A gate that leaves worktrees registered behind it turns a green run into
    housekeeping — and `git worktree list` is how a human reads this repo."""
    merge_sha = _repo_with_a_merge_left_behind(repo)
    before = git(repo, "worktree", "list").stdout

    _bash(
        f'. "{MARKER_LIB}"; gate_run_and_stamp_rev done-gate "{merge_sha}" "next"',
        repo,
        lib=GATE_LIB,
    )

    assert git(repo, "worktree", "list").stdout == before


def test_the_rev_road_reads_the_dispatcher_of_the_tree_it_judges(repo: Path):
    """ADR-0023 D7: the composition belongs to the content under judgement. Here
    the two disagree on purpose — the checkout has moved to a dispatcher naming
    other stages, and the marker must record the merge commit's."""
    merge_sha = _repo_with_a_merge_left_behind(repo)
    _commit_dispatcher(repo, "three", "the composition changed after the merge")

    ran = _bash(
        f'. "{MARKER_LIB}"; gate_run_and_stamp_rev done-gate "{merge_sha}" "next"',
        repo,
        lib=GATE_LIB,
    )

    assert ran.returncode == 0, ran.stdout + ran.stderr
    tree = _tree(repo, merge_sha)
    assert _bash(f'gate_marker_ok . "{tree}" one two', repo).returncode == 0
    assert _bash(f'gate_marker_ok . "{tree}" three', repo).returncode != 0, (
        "`three` is what the checkout asks for today, not what the judged tree ran"
    )


def test_a_swept_worktree_with_a_merge_behind_it_is_judged_not_waved_through(repo: Path):
    """The same hole one step narrower. A state record can outlive its directory
    (TMPDIR swept, discarded by hand), and that pass was written when it meant
    "nothing entered master". With a merge record in hand it means the opposite,
    and passing on the stale half is exactly the wave-through this closes."""
    merge_sha = _repo_with_a_merge_left_behind(repo)
    gone = str(repo / "worktrees" / "hats-999")

    checked = subprocess.run(
        ["bash", str(SKILL_SRC / "hooks" / "done-gate.sh"), "--check"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "AI_HATS_PROJECT_DIR": str(repo),
            "AI_HATS_TASK_ID": "HATS-999",
            "AI_HATS_WORKTREE_PATH": gone,
            "AI_HATS_MERGED_SHA": merge_sha,
        },
    )

    assert checked.returncode == 2, f"a refusal on the checks channel, not {checked.returncode}"
    assert merge_sha in checked.stdout, "and it names the merge commit as the subject"


def test_a_marker_naming_another_tree_contributes_nothing_to_the_union(repo: Path):
    """A marker whose filename and recorded `tree=` disagree certifies content it
    does not name. It drops out of the union rather than failing the whole read:
    another gate's honest marker for the same tree still counts."""
    tree = _tree(repo)
    forged = _bash(f'gate_marker_path done-gate . "{tree}"', repo).stdout.strip()
    Path(forged).parent.mkdir(parents=True, exist_ok=True)
    Path(forged).write_text("tree=deadbeef\nstages=lint unit integration\n", encoding="utf-8")

    ok = _bash(f'gate_marker_ok . "{tree}" lint', repo)

    assert ok.returncode != 0, "a marker that names another tree certifies nothing here"
