"""e2e (HATS-1853)

flow:   a maintainer runs the pre-push bundle, which must refuse the push when a
        tracker id has crept back into prose the library ships to other projects
cmds:
    bash scripts/ci-local.sh ticket-ids           # announces the stage it dispatched to
    bash scripts/ci-local.sh no-such-stage        # exit 2, and the usage names the stage
    bash scripts/ci-local.sh --stages merge-gate  # the stage is part of a gate
expect: the stage is reachable through the dispatcher, announces itself as
        `[ci-local] ticket-ids`, reports on every run what it does NOT cover and
        how many ids the pattern still finds where history lives, and is named in
        the merge-gate composition. Whether the live corpus is clean belongs to
        the stage; the refusal is proved against a planted tree instead, which no
        sibling session can change under us.
why:    the checker's own silence is the thing under test. 177 ids had
        accumulated in this library while a rule actively prescribed the form,
        and every gate stayed green through all of them because none read prose
        for what it must NOT carry. A checker that is wired but never refuses
        anything reproduces exactly that, and the ONE id this repo legitimately
        keeps is the reason a blanket "no matches ever" assertion would not do.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_RELPATH = "packages/ai-hats-library/src/ai_hats_library"


def _stage(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", "scripts/ci-local.sh", name, *args],  # noqa: S607
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "scripts/check_no_ticket_ids.py", str(root)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _plant(tmp_path: Path, body: str) -> Path:
    """A tree with a library skill and the control corpus the gate needs alive."""
    root = tmp_path / "planted"
    skill = root / LIB_RELPATH / "core" / "skills" / "demo"
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text(body)
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-x.md").write_text("Recorded in HATS-1.\n")
    return root


def test_the_gate_dispatches_to_the_ticket_check():
    """The announce IS the dispatch proof: an unwired stage exits 2 without it."""
    done = _stage("ticket-ids")
    combined = done.stdout + done.stderr
    assert "[ci-local] ticket-ids" in combined, combined
    assert done.returncode != 2, combined


def test_the_run_states_its_reach_and_that_the_pattern_is_alive():
    """Two claims a silent gate cannot make, so both are printed every run.

    The `not covered` lines fence what the stage declines to judge; the positive
    control proves the pattern still matches where ids legitimately live. Without
    the second, a green run and a dead regex read identically.
    """
    run = _stage("ticket-ids")
    combined = run.stdout + run.stderr
    assert "not covered:" in combined, combined
    assert "positive control: the pattern still finds" in combined, combined


def test_unknown_stage_lists_the_ticket_stage():
    """Deleting `ci_ticket_ids` drops the stage from this list too."""
    missing = _stage("no-such-stage")
    combined = missing.stdout + missing.stderr
    assert missing.returncode == 2, combined
    listed = [
        line.split(":", 1)[1].split()
        for line in combined.splitlines()
        if line.strip().startswith("stages:")
    ]
    assert listed and "ticket-ids" in listed[0], combined


def test_the_stage_is_part_of_the_merge_gate():
    """Wired but ungated gates nothing."""
    composition = _stage("--stages", "merge-gate")
    combined = composition.stdout + composition.stderr
    assert "ticket-ids" in combined, combined


def test_the_checker_refuses_a_planted_id_and_spares_the_placeholder(tmp_path: Path):
    """The refusal, as a real subprocess, against a corpus we broke ourselves.

    Exit 0 on the live checkout proves nothing — a checker that never finds
    anything passes that too. Both forms sit on one line so a single run has to
    discriminate rather than judge the file.
    """
    root = _plant(tmp_path, "Run `rack ls HATS-NNN`; the form landed in HATS-1430.\n")
    run = _checker(root)
    combined = run.stdout + run.stderr
    assert run.returncode == 1, combined
    fails = [line for line in combined.splitlines() if "FAIL" in line]
    assert len(fails) == 1, combined
    assert "HATS-1430" in fails[0] and "HATS-NNN" not in fails[0], combined


def test_the_checker_accepts_the_same_tree_once_the_id_is_gone(tmp_path: Path):
    """The other direction of the same planted tree — a gate that only ever
    refuses is as useless as one that only ever passes."""
    root = _plant(tmp_path, "Run `rack ls HATS-NNN`; see the ADR-0005 record.\n")
    run = _checker(root)
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "ok: no tracker id" in combined, combined
