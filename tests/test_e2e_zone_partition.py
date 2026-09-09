"""The zone stages cover the e2e tier: nothing falls out, and the default holds no zone.

The card gates ask for the halves and `push-gate` for their union, so a zone
expression that dropped a test would narrow all of them at once and nothing
would say so — a gate requiring a smaller set still passes. Selections are read
from THE STAGES via `--collect-only`; a copy of their marker expressions here
would be the defect this file exists to prevent.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES = REPO_ROOT / "scripts" / "gates.sh"
PUSH_GATE = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills"
    "/quality-gate/git_hooks/pre-push-e2e-master.sh"
)

#: The whole tier, and the parts that must add back up to it: the default and
#: every zone the table declares. Asked of the script, never listed here — a
#: hand-kept list would leave the next zone out of the union it must satisfy,
#: and out of the push gate that must require it.
WHOLE = "e2e"


def _parts() -> tuple[str, ...]:
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(GATES), "zones"], capture_output=True, text=True, check=True
    )
    stages = {line.split("|")[2].strip() for line in out.stdout.splitlines() if line.strip()}
    assert stages, "gates.sh declares no zone"
    return ("e2e-default", *sorted(stages))


PARTS = _parts()


@functools.lru_cache(maxsize=None)
def _selected(stage: str) -> frozenset[str]:
    """Every node id `stage` would run, asked of the stage itself.

    `--collect-only` rides in through the environment because a stage runs bare —
    `gates.sh` refuses argv after a stage name, which is what keeps a marker from
    certifying a narrower run than its name claims. Cached: collecting the tier
    costs seconds, and these tests ask for the same three sets.
    """
    env = {**os.environ, "PYTEST_ADDOPTS": "--collect-only"}
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(GATES), stage],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=env,
    )
    ids = frozenset(line.strip() for line in out.stdout.splitlines() if "::" in line)
    # rc=5 is "no tests collected" — a real answer; anything else is the stage
    # failing to run, and no set law should be read from that.
    assert out.returncode in (0, 5), (
        f"{stage} could not collect (rc={out.returncode}): {out.stderr[-2000:]!r}"
    )
    return ids


def test_the_zone_stages_add_back_up_to_the_whole_tier():
    """Set equality, both directions: a test in no part would never run on a card
    gate, and one claimed by a part the tier does not hold is a part selecting
    something the tier's own expression does not."""
    whole = _selected(WHOLE)
    parts = {stage: _selected(stage) for stage in PARTS}
    union: set[str] = set().union(*parts.values())

    assert union == whole, {
        "in the tier, claimed by no part": sorted(whole - union),
        "claimed by a part, not in the tier": sorted(union - whole),
    }


def test_the_default_claims_nothing_a_zone_claims():
    """`e2e-default` is defined as the tier minus every zone, so an overlap means
    that subtraction stopped matching the zones it subtracts — and the tests in
    it would move to `->done` while still being demanded at `->merge`.

    Two ZONES may overlap: a test can assert two surfaces, and each of them is
    then expected to break it. Paying for it twice is what the tier memo
    prevents (`tests/test_tier_memo.py`), not what this law forbids."""
    default = _selected("e2e-default")
    for stage in PARTS:
        if stage == "e2e-default":
            continue
        both = default & _selected(stage)
        assert not both, f"e2e-default and {stage} both claim: {sorted(both)}"


def test_the_push_gate_requires_every_part_of_the_partition():
    """It asks about the tree going to origin/master, and used to name the tier
    under one stage. The parts are the same tests, so requiring them instead
    spares a card tree a second run of what it already earned — but only while
    this list is complete. A zone declared and left out here would leave the push
    gate demanding less than the tier it stands for; `PARTS` itself is held
    complete by the union test above."""
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(PUSH_GATE), "--stages"], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    required = set(out.stdout.split())

    assert set(PARTS) <= required, sorted(set(PARTS) - required)


def test_every_part_claims_something():
    """A zone whose marker stopped applying selects nothing, and an empty part
    satisfies every set law above while the gate it feeds asks for nothing."""
    for stage in PARTS:
        assert _selected(stage), f"{stage} selects no test at all"
