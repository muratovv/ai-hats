"""e2e (HATS-2117)

flow:   a maintainer runs the pre-push bundle, which must refuse the push when
        a library skill description is not a folded strip scalar
cmds:
    bash scripts/gates.sh skill-desc       # announces the stage it dispatched to
    bash scripts/gates.sh no-such-stage    # exit 2, and the usage names the stage
    hooks/merge-gate.sh --stages           # the stage is part of a gate
expect: the stage is reachable through the dispatcher, announces itself as
        `[gates] skill-desc`, states on every run what it does not cover, and
        is named in the merge-gate composition. Whether the live corpus is
        clean belongs to the stage; the refusal is proved against a planted
        tree instead, which no sibling session can change under us.
why:    a checker that is wired but never refuses anything leaves the next
        skill free to bring back a quoted description. The one spelling this
        tree keeps is the reason a blanket "no matches ever" assertion would
        not show that the judge can still see the other spellings.
"""  # comment-length: allow — the flow block is the catalog's source

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gates]

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_RELPATH = "packages/ai-hats-library/src/ai_hats_library"


def _stage(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", "scripts/gates.sh", name, *args],  # noqa: S607
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_the_gate_dispatches_to_the_description_check():
    """The announce is the dispatch proof: an unwired stage exits 2 without it."""
    done = _stage("skill-desc")
    combined = done.stdout + done.stderr
    assert "[gates] skill-desc" in combined, combined
    assert done.returncode != 2, combined
    assert done.returncode == 0, combined


def test_the_run_states_what_it_does_not_cover():
    run = _stage("skill-desc")
    combined = run.stdout + run.stderr
    assert "not covered:" in combined, combined
    assert "role and trait injections" in combined, combined


def test_unknown_stage_lists_the_description_stage():
    missing = _stage("no-such-stage")
    combined = missing.stdout + missing.stderr
    assert missing.returncode == 2, combined
    listed = [
        line.split(":", 1)[1].split()
        for line in combined.splitlines()
        if line.strip().startswith("stages:")
    ]
    assert listed and "skill-desc" in listed[0], combined


def test_the_stage_is_part_of_the_merge_gate():
    """Wired but ungated is a stage nothing runs."""
    gate = (
        REPO_ROOT
        / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate"
        / "hooks/merge-gate.sh"
    )
    composition = subprocess.run(
        ["bash", str(gate), "--stages"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "skill-desc" in composition.stdout.split(), composition.stdout


def test_the_checker_refuses_a_planted_quoted_description(tmp_path: Path):
    """HATS-2117: the refusal has to be shown on a defect we planted."""
    root = tmp_path / "planted"
    bad = root / LIB_RELPATH / "core" / "skills" / "bad"
    good = root / LIB_RELPATH / "core" / "skills" / "good"
    bad.mkdir(parents=True)
    good.mkdir(parents=True)
    (bad / "SKILL.md").write_text('---\nname: bad\ndescription: "nope"\n---\n', encoding="utf-8")
    (good / "SKILL.md").write_text(
        "---\nname: good\ndescription: >-\n  fine\n---\n", encoding="utf-8"
    )

    run = subprocess.run(  # noqa: S603
        [sys.executable, "scripts/check_skill_desc.py", str(root)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = run.stdout + run.stderr
    assert run.returncode == 1, combined
    assert "skills/bad/SKILL.md" in combined, combined
    assert "skills/good/SKILL.md" not in combined, combined
