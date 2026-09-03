"""The gate table in `scripts/gates.sh` and the stage runner agree, both ways.

A gate is a name for a set of stages, and the table is the one home of that
fact. What can rot around it: a stage the runner grew that no row names (the
"named nowhere" drift ADR-0023 D4 suffered — 7 of 23 stages), a row naming a
stage the runner does not have, and the containment the marker's absorption
rule stands on (review ⊆ merge ⊆ done). Each is a subprocess of the two
scripts, never a literal copied here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES = REPO_ROOT / "scripts" / "gates.sh"
CI_LOCAL = REPO_ROOT / "scripts" / "ci-local.sh"

#: Runner verbs that are not stages yet take the stage slot — the table lists
#: them with `-` so a reader sees they are deliberate.
NON_STAGE_ROWS = {"prepare"}


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(script), *args], capture_output=True, text=True, check=False
    )


def _roster() -> list[str]:
    out = _run(GATES, "list")
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def _stages(gate: str) -> list[str]:
    out = _run(GATES, "stages", gate)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def _rows() -> list[tuple[str, list[str], str]]:
    out = _run(GATES, "table")
    assert out.returncode == 0, out.stderr
    rows = []
    for line in out.stdout.splitlines():
        stage, gates, desc = (cell.strip() for cell in line.split("|", 2))
        rows.append((stage, gates.split(), desc))
    return rows


def _runner_stages() -> set[str]:
    """What the dispatcher itself lists when refused an unknown stage."""
    out = _run(CI_LOCAL, "no-such-stage")
    text = out.stdout + out.stderr
    assert "stages:" in text, text
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("stages:"))
    return set(line.split(":", 1)[1].split())


def test_the_roster_is_the_four_gates_and_every_one_resolves():
    roster = _roster()

    assert roster == ["review-gate", "merge-gate", "done-gate", "push-gate"]
    for gate in roster:
        assert _stages(gate), f"{gate} names no stage"


def test_every_stage_a_row_names_is_a_stage_the_runner_has():
    known = _runner_stages() | NON_STAGE_ROWS

    phantom = {stage for stage, _gates, _desc in _rows()} - known

    assert not phantom, f"the table names stages the runner does not have: {sorted(phantom)}"


def test_every_stage_the_runner_has_is_named_by_a_row():
    """The "named nowhere" refusal: a new `ci_*` function must take a position
    in the table, even if that position is `-`."""
    named = {stage for stage, _gates, _desc in _rows()}

    unnamed = _runner_stages() - named

    assert not unnamed, (
        f"the runner has stages the table does not place: {sorted(unnamed)} — "
        "add a row, with `-` if no gate requires it"
    )


def test_every_row_names_gates_the_roster_has_or_the_dash():
    roster = set(_roster())
    for stage, gates, _desc in _rows():
        for gate in gates:
            assert gate == "-" or gate in roster, f"{stage} names an unknown gate {gate!r}"
        assert gates, f"{stage} has an empty gates column — write `-`"


def test_every_row_carries_a_description():
    for stage, _gates, desc in _rows():
        assert desc, f"{stage} has no description — the ADR's stage table renders from it"


def test_review_and_merge_demand_the_same_set():
    """They differ in the edge they sit on, not in what they demand; equality is
    what lets one run satisfy both, and `unit` is the stage that made the
    review gate worth having."""
    review, merge = set(_stages("review-gate")), set(_stages("merge-gate"))

    assert review == merge
    assert "unit" in review


def test_the_card_gates_nest_so_one_run_of_the_widest_pays_for_all():
    """Absorption is set inclusion over per-stage markers (ADR-0023 D5)."""
    review, merge, done = (set(_stages(g)) for g in ("review-gate", "merge-gate", "done-gate"))

    assert review <= merge <= done, {
        "only in review": sorted(review - merge),
        "only in merge": sorted(merge - done),
    }


def test_the_done_gate_demands_what_only_it_can_ask():
    """A superset by at least one stage, or the gate keeps passing on a run it
    never demanded and nothing turns red."""
    assert set(_stages("done-gate")) - set(_stages("merge-gate"))


def test_a_precondition_is_never_part_of_a_verdict():
    """`prepare` asserts nothing; a gate naming it would count a venv build as
    evidence. Every gate on the roster, not a hand-kept three."""
    for gate in _roster():
        assert "prepare" not in _stages(gate), f"{gate} names a precondition as a stage"


def test_the_dispatcher_shim_answers_with_the_table():
    """`ci-local.sh --stages <gate>` is kept for the library hooks that still
    ask it; it must say exactly what the table says."""
    for gate in _roster():
        shim = _run(CI_LOCAL, "--stages", gate)
        assert shim.returncode == 0, shim.stderr
        assert shim.stdout.split() == _stages(gate)


@pytest.mark.parametrize(
    "argv", [(), ("stages",), ("stages", "no-such-gate"), ("check", "done-gate", "unit")]
)
def test_usage_errors_exit_64(argv: tuple[str, ...]):
    out = _run(GATES, *argv)

    assert out.returncode == 64, (argv, out.stderr)
