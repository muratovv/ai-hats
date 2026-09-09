"""`gates.sh touched` turns a diff into the zone stages a gate must additionally require.

The one thing it must never do is answer quietly when it cannot tell: "nothing
changed" and "I could not name a base" are the same empty stdout, and a gate
reading the second as the first passes a change it never examined. Every case
below therefore checks the exit code as well as the output.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES = REPO_ROOT / "scripts" / "gates.sh"
WT_MANAGER = REPO_ROOT / "packages/ai-hats-wt/src/ai_hats_wt/manager.py"

#: The zone the shipped table declares, and a path it owns.
ZONE_STAGE = "e2e-rack"
ZONE_PATH = "packages/ai-hats-rack/src/ai_hats_rack/cli.py"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _touched(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", "scripts/gates.sh", "touched", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository carrying the REAL `gates.sh`, so the shipped zone table is
    what answers — a fixture with its own table would test a copy."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _write(repo, "scripts/gates.sh", GATES.read_text(encoding="utf-8"))
    _write(repo, ZONE_PATH, "x = 1\n")
    _write(repo, "src/other.py", "y = 1\n")
    _commit(repo, "base")
    return repo


def test_a_change_inside_a_zone_demands_that_zones_stage(repo: Path):
    _git(repo, "checkout", "-q", "-b", "task/x")
    _write(repo, ZONE_PATH, "x = 2\n")
    _commit(repo, "touch the zone")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [ZONE_STAGE]


def test_a_change_outside_every_zone_demands_nothing(repo: Path):
    _git(repo, "checkout", "-q", "-b", "task/x")
    _write(repo, "src/other.py", "y = 2\n")
    _commit(repo, "touch nothing zoned")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


def test_a_merge_commit_is_judged_against_its_first_parent(repo: Path):
    """What the card contributed, not what the base branch has moved on to. The
    two answers differ here on purpose: the merge IS the tip of master, so a
    merge-base against master would diff the commit with itself and find
    nothing."""
    _git(repo, "checkout", "-q", "-b", "task/x")
    _write(repo, ZONE_PATH, "x = 2\n")
    _commit(repo, "touch the zone")
    _git(repo, "checkout", "-q", "master")
    _write(repo, "src/other.py", "y = 2\n")
    _commit(repo, "master moves")
    _git(
        repo,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "merge",
        "-q",
        "--no-ff",
        "task/x",
        "-m",
        "merge",
    )
    merge_sha = _git(repo, "rev-parse", "HEAD")

    out = _touched(repo, "--rev", merge_sha)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [ZONE_STAGE]


def test_an_explicit_base_overrides_both_roads(repo: Path):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, ZONE_PATH, "x = 2\n")
    _commit(repo, "touch the zone on the base branch itself")

    out = _touched(repo, "--base", base)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [ZONE_STAGE]


def test_a_base_it_cannot_name_refuses_instead_of_printing_nothing(repo: Path):
    """The failure this whole file exists for: silence would read as "no zone
    demanded" and the gate would require nothing."""
    _git(repo, "branch", "-m", "master", "elsewhere")

    out = _touched(repo)

    assert out.returncode != 0
    assert out.stdout.strip() == ""
    assert "cannot tell what changed" in out.stderr


def test_every_zone_owns_at_least_one_tracked_file_here():
    """A prefix that matches nothing can never be demanded — a zone declared and
    unreachable, the hole this table grows most easily.

    Checked HERE and not inside `touched`, which runs against whatever tree is
    being judged: a scratch project carrying this repo's `gates.sh` has none of
    these paths, and refusing there broke the gate for every test that plants
    it. The table belongs to this repository, so this repository checks it."""
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(GATES), "zones"], capture_output=True, text=True, check=True
    )
    rows = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert rows, "gates.sh declares no zone"
    for row in rows:
        prefix, marker, _stage = (cell.strip() for cell in row.split("|"))
        tracked = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(REPO_ROOT), "ls-files", "--", prefix],
            capture_output=True,
            text=True,
            check=True,
        )
        assert tracked.stdout.strip(), f"zone {marker!r} owns {prefix!r}, which is empty here"


def test_the_shell_base_branches_match_the_ones_ai_hats_wt_resolves():
    """Two copies of the base-branch list, and a drifted one names the wrong
    base — which is a wrong diff, which is a wrong set of required stages."""
    shell = re.search(r"^CANONICAL_BASE_BRANCHES='([^']*)'$", GATES.read_text(), re.M)
    assert shell, "gates.sh declares no CANONICAL_BASE_BRANCHES"
    python = re.search(
        r"^CANONICAL_BASE_BRANCHES: tuple\[str, \.\.\.\] = \(([^)]*)\)",
        WT_MANAGER.read_text(),
        re.M,
    )
    assert python, "ai_hats_wt.manager declares no CANONICAL_BASE_BRANCHES"

    assert shell.group(1).split() == re.findall(r'"([^"]+)"', python.group(1))
