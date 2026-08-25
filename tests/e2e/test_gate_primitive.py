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
import time
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


def _bash(
    script: str,
    cwd: Path,
    lib: Path = MARKER_LIB,
    env: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Source one library and run `script` under a real bash.

    `env` overlays the inherited environment; a `None` value removes the name.
    """
    child = os.environ.copy()
    for name, value in (env or {}).items():
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail; . "{lib}"\n{script}'],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=child,
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
    not what the branch holds.

    rc NONZERO (HATS-1819): what the caller asked for is a marker, and it did not
    get one — the same outcome the marker-unwritable branch already reported as
    failure. Returning 0 also let each caller's ``||`` fall through to its success
    line, so a dirty run printed "NO marker written" and "passes instantly" in
    consecutive lines.
    """
    (repo / "uncommitted.txt").write_text("dirty", encoding="utf-8")
    tree = _tree(repo)

    stamped = _bash(
        f'. "{MARKER_LIB}"; gate_stamp done-gate . "{tree}" "lint unit"', repo, lib=GATE_LIB
    )

    assert stamped.returncode != 0, "a run that earned no marker must not report success"
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


# ---------------------------------------------------------------------------
# 6. the store is swept, so it does not grow forever (HATS-1682)
# ---------------------------------------------------------------------------


def _aged(path: Path, days: float) -> Path:
    """A marker whose own mtime sits `days` in the past — what the sweep keys on."""
    path.write_text(f"tree=aged-{days}\n", encoding="utf-8")
    when = time.time() - days * 86400
    os.utime(path, (when, when))
    return path


def test_a_write_sweeps_markers_nobody_will_come_back_for(repo: Path):
    """125 markers had piled up on one checkout, the oldest naming a tree from a
    week nobody would return to. Nothing pruned them: staleness is impossible by
    keying, so no expiry was needed — and none was written either."""
    tree = _tree(repo)
    written = _bash(f'gate_marker_write done-gate . "{tree}" "lint"', repo)
    assert written.returncode == 0, written.stderr
    directory = Path(written.stdout.strip()).parent
    ancient = directory / ("a" * 40)
    ancient.write_text("tree=old\n", encoding="utf-8")
    os.utime(ancient, (0, 0))

    fresh = _bash(f'gate_marker_write done-gate . "{tree}" "lint unit"', repo)

    assert fresh.returncode == 0, fresh.stderr
    assert not ancient.exists(), "the sweep left a marker older than the keep window"
    assert Path(fresh.stdout.strip()).is_file(), "the sweep took the marker just written"


def test_the_sweep_keeps_a_marker_inside_the_window(repo: Path):
    """The other half, and the half that pins WHERE the window falls: 29 days old
    survives the write that takes 32 days old. Both files are aged, so neither can
    pass by being new — the pair discriminates, where the "keeps" half alone did
    not (a fresh file survives with or without a sweep).

    29 and 32, not 30 and 31: `-mtime +30` rounds the age UP on BSD find and DOWN
    on GNU, so the two disagree by up to a day right at the edge."""
    tree = _tree(repo)
    written = _bash(f'gate_marker_write done-gate . "{tree}" "lint"', repo)
    assert written.returncode == 0, written.stderr
    directory = Path(written.stdout.strip()).parent
    inside = _aged(directory / ("b" * 40), 29)
    outside = _aged(directory / ("c" * 40), 32)

    fresh = _bash(f'gate_marker_write done-gate . "{tree}" "lint unit"', repo)

    assert fresh.returncode == 0, fresh.stderr
    assert inside.exists(), "29 days is inside a 30-day window — the sweep took it anyway"
    assert not outside.exists(), "32 days is past it — the sweep left it"


def test_a_garbage_keep_window_says_so_instead_of_quietly_stopping(repo: Path):
    """MEASURED before the fix: this value made `find` refuse the whole
    expression, `2>/dev/null || true` ate the error, and housekeeping stopped for
    good with no signal on any channel. A gate that stopped acting must not look
    like a gate with nothing to do. It still may not fail the run that earned the
    marker — hence `set -e` here, and the rc assertions."""
    tree = _tree(repo)
    written = _bash(f'gate_marker_write done-gate . "{tree}" "lint"', repo)
    assert written.returncode == 0, written.stderr
    directory = Path(written.stdout.strip()).parent
    ancient = _aged(directory / ("d" * 40), 99)

    fresh = _bash(
        f'set -e; gate_marker_write done-gate . "{tree}" "lint unit"',
        repo,
        env={"AI_HATS_GATE_MARKER_KEEP_DAYS": "+1 -o -name '*'"},
    )

    assert fresh.returncode == 0, f"housekeeping killed the run: {fresh.stderr}"
    assert "AI_HATS_GATE_MARKER_KEEP_DAYS" in fresh.stderr, "a garbage window must name itself"
    assert not ancient.exists(), "falling back to the default still sweeps"
    assert Path(fresh.stdout.strip()).is_file(), "the marker just earned is on disk"


def test_sourcing_the_library_defines_no_keep_window_in_the_caller_s_scope(repo: Path):
    """The header promises a file that changes nothing in its caller's scope.
    `: "${AI_HATS_GATE_MARKER_KEEP_DAYS:=30}"` at file scope broke that promise —
    and left the sweep depending on a name someone else owns, so a caller that
    unsets it takes the whole write down under `set -u`. A `local` read per call
    owes the caller nothing."""
    tree = _tree(repo)
    no_window: dict[str, str | None] = {"AI_HATS_GATE_MARKER_KEEP_DAYS": None}
    written = _bash(f'gate_marker_write done-gate . "{tree}" "lint"', repo, env=no_window)
    assert written.returncode == 0, written.stderr
    directory = Path(written.stdout.strip()).parent
    ancient = _aged(directory / ("e" * 40), 99)

    leaked = _bash('echo "[${AI_HATS_GATE_MARKER_KEEP_DAYS-unset}]"', repo, env=no_window)
    hostile = _bash(
        f'unset AI_HATS_GATE_MARKER_KEEP_DAYS\ngate_marker_write done-gate . "{tree}" "lint unit"',
        repo,
        env=no_window,
    )

    assert leaked.stdout.strip() == "[unset]", "sourcing must not define the name in the caller"
    assert hostile.returncode == 0, f"the caller's `unset` killed the write: {hostile.stderr}"
    assert not ancient.exists(), "the default window still applies"
    assert Path(hostile.stdout.strip()).is_file(), "the marker just earned is on disk"
