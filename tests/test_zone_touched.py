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


def _rows() -> list[tuple[str, str, str]]:
    """Every `prefix | marker | stage` the shipped table declares.

    Asked of the script, never listed here: a zone spans one row per prefix, so a
    copy in this file would go stale on the next prefix a subject grows — which is
    exactly the row whose wiring nothing would then check.
    """
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(GATES), "zones"], capture_output=True, text=True, check=True
    )
    rows = [tuple(cell.strip() for cell in ln.split("|")) for ln in out.stdout.splitlines() if ln.strip()]
    assert rows, "gates.sh declares no zone"
    for row in rows:
        assert len(row) == 3, f"not a `prefix | marker | stage` row: {row!r}"
    return rows  # type: ignore[return-value]


#: A tier test carrying a zone marker, one carrying none, and one carrying the
#: marker from outside the tier's own roots (`E2E_PATHS`).
ZONED_TEST = "tests/e2e/test_zoned_probe.py"
UNZONED_TEST = "tests/e2e/test_unzoned_probe.py"
OFF_TIER_TEST = "tests/test_off_tier_probe.py"


def _probe_for(prefix: str) -> str:
    """A path this prefix owns — a file to plant so the prefix has something to
    match. Prefixes come in three shapes: a directory (`packages/ai-hats-rack/`),
    a whole file (`src/ai_hats/tracker_wiring.py`) and a name stem
    (`src/ai_hats/rack_`); only the middle one is already a path."""
    if prefix.endswith(".py"):
        return prefix
    return f"{prefix}probe.py" if prefix.endswith("/") else f"{prefix}_probe.py"


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
    for prefix, _marker, _stage in _rows():
        _write(repo, _probe_for(prefix), "x = 1\n")
    _write(repo, "src/other.py", "y = 1\n")
    _write(repo, ZONED_TEST, f"pytestmark = pytest.mark.{_rows()[0][1]}\n")
    _write(repo, UNZONED_TEST, "pytestmark = pytest.mark.integration\n")
    _write(repo, OFF_TIER_TEST, f"pytestmark = pytest.mark.{_rows()[0][1]}\n")
    _commit(repo, "base")
    return repo


def test_a_change_inside_a_zone_demands_that_zones_stage(repo: Path):
    _git(repo, "checkout", "-q", "-b", "task/x")
    _write(repo, ZONE_PATH, "x = 2\n")
    _commit(repo, "touch the zone")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [ZONE_STAGE]


@pytest.mark.parametrize("row", _rows(), ids=lambda row: f"{row[2]}:{row[0]}")
def test_every_prefix_the_table_declares_demands_its_stage(row: tuple[str, str, str], repo: Path):
    """One row is one prefix, and a zone is every row naming its marker. A prefix
    wired nowhere is a path the table promises to watch and does not — invisible,
    because the zone keeps working through its other rows."""
    prefix, _marker, stage = row
    _git(repo, "checkout", "-q", "-b", "task/x")
    _write(repo, _probe_for(prefix), "x = 2\n")
    _commit(repo, f"touch {prefix}")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    assert stage in out.stdout.split(), (out.stdout, out.stderr)


def test_a_stage_two_touched_prefixes_share_is_named_once(repo: Path):
    """The caller appends this output to a gate's stage list, so a stage named
    twice is a stage looked up twice — and a zone with several prefixes is the
    normal case, not the exotic one."""
    rows = _rows()
    by_stage: dict[str, list[str]] = {}
    for prefix, _marker, stage in rows:
        by_stage.setdefault(stage, []).append(prefix)
    shared = {stage: prefixes for stage, prefixes in by_stage.items() if len(prefixes) > 1}
    assert shared, "no zone declares two prefixes — this test would prove nothing"

    _git(repo, "checkout", "-q", "-b", "task/x")
    for prefixes in shared.values():
        for prefix in prefixes:
            _write(repo, _probe_for(prefix), "x = 2\n")
    _commit(repo, "touch several prefixes of the same zone")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    named = out.stdout.split()
    assert len(named) == len(set(named)), named
    for stage in shared:
        assert named.count(stage) == 1, (stage, named)


def test_a_card_editing_a_zoned_test_demands_that_zone(repo: Path):
    """The second road in. The table's prefixes name source, and a zoned test is
    already subtracted from `e2e-default` — so without this the test a card
    writes runs on no card gate at all, and first executes on `push-gate`, after
    the merge it was written for."""
    stage = _rows()[0][2]
    _git(repo, "checkout", "-q", "-b", "task/x")
    _write(repo, ZONED_TEST, "pytestmark = pytest.mark.%s  # edited\n" % _rows()[0][1])
    _commit(repo, "edit a zoned test")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [stage]


def test_a_deleted_zoned_test_is_read_from_the_base(repo: Path):
    """It is gone at the subject, so the zone it declared can only be read where
    it still stands. Refusing here would refuse a legal card."""
    stage = _rows()[0][2]
    _git(repo, "checkout", "-q", "-b", "task/x")
    (repo / ZONED_TEST).unlink()
    _commit(repo, "delete a zoned test")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [stage]


def test_an_unmarked_tier_test_demands_nothing(repo: Path):
    """It is what `e2e-default` is made of, and that stage stands on `->done`
    already — demanding a zone for it would name a stage no marker claims."""
    _git(repo, "checkout", "-q", "-b", "task/x")
    _write(repo, UNZONED_TEST, "pytestmark = pytest.mark.integration  # edited\n")
    _commit(repo, "edit an unmarked tier test")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


def test_a_marked_file_outside_the_tier_demands_nothing(repo: Path):
    """The zone stages select over `E2E_PATHS` and nothing else, so a marker
    written elsewhere selects no test — demanding a stage for it would ask a gate
    to run a set that cannot contain the file that asked."""
    _git(repo, "checkout", "-q", "-b", "task/x")
    _write(repo, OFF_TIER_TEST, "pytestmark = pytest.mark.%s  # edited\n" % _rows()[0][1])
    _commit(repo, "edit a marked file outside the tier")

    out = _touched(repo)

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


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
    it. The table belongs to this repository, so this repository checks it.

    Matched the way `touched` matches — a plain string prefix over the tracked
    paths, not a git pathspec. `git ls-files -- src/ai_hats/rack_` answers
    nothing for a prefix that names four files, so a pathspec here would refuse
    a row the verb honours: a guard asserting something other than what it
    guards."""
    tracked = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for prefix, marker, _stage in _rows():
        assert any(path.startswith(prefix) for path in tracked), (
            f"zone {marker!r} owns {prefix!r}, which is empty here"
        )


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
