"""e2e (HATS-1825)

flow:   a maintainer runs the pre-push bundle, which must refuse the push when a
        path, a library prefix, a section or a code symbol named in library prose
        no longer resolves
cmds:
    bash scripts/gates.sh prose-refs      # announces the stage it dispatched to
    bash scripts/gates.sh no-such-stage   # exit 2, and the usage names the stage
    hooks/merge-gate.sh --stages              # the stage is part of a gate
expect: the stage is reachable through the dispatcher, announces itself as
        `[gates] prose-refs`, states on every run what it does NOT cover, and
        is named in the merge-gate composition. Whether the live corpus is INTACT
        belongs to the stage, not here (HATS-1714/1716) — the refusal is proved
        instead against a planted tree, which no sibling session can change
why:    the checker's own silence is the thing under test. HATS-1823 measured 21
        references in this library that did not resolve, and every gate in the
        repo stayed green through all of them, because none reads prose. A
        checker that is wired but never refuses anything reproduces exactly that.
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
    return subprocess.run(
        ["bash", "scripts/gates.sh", name, *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_gate_dispatches_to_the_prose_check():
    """The announce IS the dispatch proof: an unwired stage exits 2 without it."""
    done = _stage("prose-refs")
    combined = done.stdout + done.stderr
    assert "[gates] prose-refs" in combined, combined
    assert done.returncode != 2, combined


def test_the_run_states_what_it_does_not_cover():
    """Reach is a contract here, not a courtesy.

    The checker judges four reference shapes and deliberately skips `~` paths,
    gitignored paths, unanchored paths and fenced samples. Silence about that
    would be indistinguishable from coverage — the failure HATS-1823 found.
    """
    run = _stage("prose-refs")
    combined = run.stdout + run.stderr
    assert "not covered:" in combined, combined
    assert "unanchored paths not judged" in combined, combined


def test_unknown_stage_lists_the_prose_stage():
    """Deleting `ci_prose_refs` drops the stage from this list too."""
    missing = _stage("no-such-stage")
    combined = missing.stdout + missing.stderr
    assert missing.returncode == 2, combined
    listed = [
        line.split(":", 1)[1].split()
        for line in combined.splitlines()
        if line.strip().startswith("stages:")
    ]
    assert listed and "prose-refs" in listed[0], combined


def test_the_stage_is_part_of_the_merge_gate():
    """Wired but ungated is the `check_dependency_floor.py` failure (HATS-1373):
    a stage nothing runs gates nothing."""
    gate = (
        Path(__file__).resolve().parents[2]
        / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate"
        / "hooks/merge-gate.sh"
    )
    composition = subprocess.run(
        ["bash", str(gate), "--stages"], capture_output=True, text=True, check=True
    )
    assert "prose-refs" in composition.stdout.split(), composition.stdout


def test_the_checker_refuses_a_planted_dead_reference(tmp_path: Path):
    """The positive control, run as a real subprocess against a planted tree.

    Asserting exit 0 on the live checkout proves nothing about the checker — a
    checker that returns no findings ever passes that. The refusal has to be
    demonstrated on a corpus whose one defect we put there ourselves, and the
    same run must leave the live reference beside it alone. HATS-1825 built the
    checker before the cleanup for this reason.
    """
    root = tmp_path / "planted"
    skill = root / LIB_RELPATH / "core" / "skills" / "demo"
    skill.mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "real.md").write_text("# real\n")
    skill.joinpath("SKILL.md").write_text(
        "---\nname: demo\n---\n\nSee `docs/real.md` and `docs/vanished.md`.\n"
    )
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "planted",
        ],
        check=True,
        capture_output=True,
    )

    run = subprocess.run(
        [sys.executable, "scripts/check_prose_refs.py", str(root)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = run.stdout + run.stderr
    assert run.returncode == 1, combined
    assert "docs/vanished.md" in combined, combined
    assert "docs/real.md" not in combined, combined
