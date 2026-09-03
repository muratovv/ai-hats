"""e2e (HATS-1591)

flow:   a maintainer pushes to master, and the pre-push bundle must refuse the
        push when a source file carries a bidirectional control — a character
        that changes how the line RENDERS but not how it parses, so review
        cannot see it
cmds:
    bash scripts/gates.sh bidi              # exit 0 while the tree is clean
    git_hooks/pre-push-e2e-master.sh --stages # the push gate names `bidi`
    bash scripts/gates.sh no-such-stage     # exit 2, and the usage names it
expect: the stage is reachable through the dispatcher, announces itself as
        `[gates] bidi`, exits 0 on a clean tree, exits 1 naming the file and
        the codepoint when one is planted, and appears in the push-gate
        composition
why:    this is the ONE check bandit held that ruff's `S` family does not
        (`B613 trojansource`); bandit itself was dropped in HATS-1591 because
        its other three exclusive checks name django, pytorch and huggingface,
        none of which this repo depends on. If this stage silently stops
        dispatching, the trade made in that card turns into a straight loss and
        nothing goes red.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

# Built from an escape on purpose: storing the literal would plant in this very
# tree the thing the stage refuses.
RLO = "\u202e"


def _stage(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/gates.sh", name],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_gate_dispatches_to_the_bidi_check():
    run = _stage("bidi")
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "[gates] bidi" in combined, combined
    assert "[bidi] ok:" in combined, combined


def test_a_planted_override_is_refused(tmp_path: Path):
    """Exit 0 on a clean tree proves dispatch, not detection."""
    planted = tmp_path / "innocent.py"
    planted.write_text(f'ACCESS = "admin"  # {RLO}\n', encoding="utf-8")

    run = subprocess.run(
        ["python3", "scripts/check_bidi.py", str(planted)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = run.stdout + run.stderr
    assert run.returncode == 1, combined
    assert "U+202E" in combined, combined
    assert "innocent.py" in combined, combined


def test_push_gate_composition_names_the_stage():
    """The gate runs what its `--stages` names, so dropping it here disarms it."""
    hook = (
        "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/maintainer-quality-gate"
        "/git_hooks/pre-push-e2e-master.sh"
    )
    listed = subprocess.run(
        ["bash", hook, "--stages"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = listed.stdout + listed.stderr
    assert listed.returncode == 0, combined
    assert "bidi" in listed.stdout.split(), combined


def test_unknown_stage_lists_the_bidi_stage():
    """Deleting the `case` branch drops the stage from this list too."""
    missing = _stage("no-such-stage")
    combined = missing.stdout + missing.stderr
    assert missing.returncode == 2, combined
    assert "bidi" in combined, combined
