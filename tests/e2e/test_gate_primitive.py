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

import subprocess
from pathlib import Path

import pytest

from _helpers.git import git, init_repo

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

    ok = _bash(f'gate_marker_ok done-gate . "{tree}" lint unit', repo)

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
    ok = _bash(f'gate_marker_ok done-gate . "{tree}" lint', repo)
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

    ok = _bash(f'gate_marker_ok done-gate . "{tree}" lint unit', repo)

    assert ok.returncode == 0, "required ⊆ recorded — the run already covered this gate"
