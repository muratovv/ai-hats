"""e2e (HATS-1921)

flow:   an agent merging a branch that changed a zone of the codebase
cmds:
    ai-hats wt merge HATS-1921
    scripts/gates.sh touched
    make merge-gate
expect: the merge gate refuses naming `e2e-rack` — a stage no gate declares,
        demanded because the branch changed `packages/ai-hats-rack/` — and the
        same branch with that one stage earned is let through; a branch that
        changed nothing zoned is never asked for it
why:    without it a change lands in master with only the tier that runs after
        the merge, so the tests its own area owns are first run when the
        breakage is already shared
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.git import commit_file, git, init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate"
MERGE_GATE = SKILL / "hooks" / "merge-gate.sh"
GATES = REPO_ROOT / "scripts" / "gates.sh"
STAGES_DIR = Path(".git") / "ai-hats" / "stages"

#: The shipped zone: the stage it demands and a path inside it.
ZONE_STAGE = "e2e-rack"
ZONE_FILE = "packages/ai-hats-rack/src/ai_hats_rack/cli.py"
#: The checks channel spells a refusal 2 and a pass 0 (ADR-0020 D2).
REFUSE, PASS = 2, 0


def _declared() -> list[str]:
    """What `merge-gate` requires of every tree, zones aside."""
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(MERGE_GATE), "--stages"], capture_output=True, text=True, check=True
    )
    return out.stdout.split()


def _check(project: Path) -> subprocess.CompletedProcess[str]:
    """The gate as the checks runner spawns it: no argv, the subject in the env."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(project),
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_TASK_ID": "HATS-1921",
        "AI_HATS_WORKTREE_PATH": str(project),
    }
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(MERGE_GATE)], capture_output=True, text=True, check=False, env=env
    )


def _missing(out: subprocess.CompletedProcess[str]) -> list[str]:
    """The stage names under `Missing:`, and not the remedy command below them —
    it is indented the same, which is how this parser first read it as a stage."""
    body = out.stdout.split("Missing:", 1)
    if len(body) == 1:
        return []
    names = []
    for line in body[1].splitlines():
        if not line.strip():
            # The tail of the `Missing:` line itself comes first and is empty;
            # the next blank line is the one that ends the block.
            if names:
                break
            continue
        names.append(line.strip())
    return names


def _stamp(project: Path, stages: list[str]) -> None:
    tree = git(project, "rev-parse", "HEAD^{tree}").stdout.strip()
    where = project / STAGES_DIR / tree
    where.mkdir(parents=True, exist_ok=True)
    for stage in stages:
        (where / stage).write_text(f"tree={tree}\nstage={stage}\n", encoding="utf-8")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project carrying the REAL `gates.sh`, so the shipped zone table answers."""
    project = tmp_path / "proj"
    (project / "scripts").mkdir(parents=True)
    init_repo(project)
    (project / "scripts" / "gates.sh").write_text(GATES.read_text(encoding="utf-8"))
    commit_file(project, ZONE_FILE, "x = 1\n", "the zone exists")
    git(project, "checkout", "-q", "-b", "task/hats-1921")
    return project


def test_a_branch_that_changed_a_zone_is_refused_for_that_zones_stage(project: Path):
    """The whole point: a stage no gate declares, demanded by the diff alone."""
    commit_file(project, ZONE_FILE, "x = 2\n", "change the rack zone")

    out = _check(project)

    assert out.returncode == REFUSE, out.stdout + out.stderr
    assert ZONE_STAGE in _missing(out)


def test_the_zone_stage_is_the_only_thing_left_between_that_branch_and_master(
    project: Path,
):
    """Stamping everything the gate DECLARES still leaves it refused, and names
    only the zone — so the refusal is the diff's doing and nothing else's."""
    commit_file(project, ZONE_FILE, "x = 2\n", "change the rack zone")
    _stamp(project, _declared())

    out = _check(project)

    assert out.returncode == REFUSE, out.stdout + out.stderr
    assert _missing(out) == [ZONE_STAGE]


def test_earning_the_zone_stage_lets_the_same_branch_through(project: Path):
    commit_file(project, ZONE_FILE, "x = 2\n", "change the rack zone")
    _stamp(project, [*_declared(), ZONE_STAGE])

    out = _check(project)

    assert out.returncode == PASS, out.stdout + out.stderr


def test_a_branch_outside_every_zone_is_never_asked_for_one(project: Path):
    """The negative control that makes the three above mean something: the same
    gate, the same stamps, a diff that touches nothing zoned."""
    commit_file(project, "src/unrelated.py", "y = 2\n", "change nothing zoned")
    _stamp(project, _declared())

    out = _check(project)

    assert out.returncode == PASS, out.stdout + out.stderr


def test_a_tree_whose_gates_predate_zones_is_told_so_rather_than_refused(project: Path):
    """A card branched before this landed has a `gates.sh` with no `touched`, and
    so no zone stage to earn either. Declared fail-open, price said out loud —
    the alternative is refusing every in-flight card until it rebases."""
    text = GATES.read_text(encoding="utf-8")
    start = text.index("cmd_touched() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    stripped = (text[:start] + text[end:]).replace(
        '    touched) shift; cmd_touched "$@"; exit 0 ;;\n', ""
    )
    assert "cmd_touched" not in stripped
    (project / "scripts" / "gates.sh").write_text(stripped, encoding="utf-8")
    commit_file(project, ZONE_FILE, "x = 2\n", "change the rack zone")
    _stamp(project, _declared())

    out = _check(project)

    assert out.returncode == PASS, out.stdout + out.stderr
    assert "predates zone selection" in out.stdout
