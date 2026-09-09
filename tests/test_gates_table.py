"""The stages `scripts/gates.sh` knows and the gates that require them agree.

A stage is a `ci_*` function and a row of `gates.sh list`; a gate is a thin
script declaring the stages it requires. What can rot between them: a function
with no row (the description the ADR renders from is missing), a row with no
function, a gate naming a stage that does not exist, a stage no gate requires
that nobody decided to leave out, and the containment the marker's absorption
rule stands on (review ⊆ merge ⊆ done). Each is a subprocess of the scripts,
never a literal copied here.

The containment holds between what the gates DECLARE. It does not hold between
what they end up requiring: `->merge` also demands the zones the diff names and
`->done` does not, which is a decision (ADR-0023 D11), asserted below.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES = REPO_ROOT / "scripts" / "gates.sh"
SKILL = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate"
#: Every gate this project has: the checks-channel ones and the git one.
GATE_SCRIPTS = {
    "review-gate": SKILL / "hooks" / "review-gate.sh",
    "merge-gate": SKILL / "hooks" / "merge-gate.sh",
    "done-gate": SKILL / "hooks" / "done-gate.sh",
    "push-gate": SKILL / "git_hooks" / "pre-push-e2e-master.sh",
}

#: Stages no gate requires, each a decision: CI-only, network, housekeeping, a
#: precondition. A new stage must join a gate or this list — never neither.
KNOWN_UNGATED = {
    "coverage",
    "security",
    "version-skew",
    "python-pin",
    "tmp-sweep",
    "prepare",
    # The whole tier under one name: CI's `e2e` job and `make e2e`. The gates
    # require its PARTS, so a tree is never asked to re-run the same tests under
    # another name (parts == whole: tests/test_e2e_zone_partition.py).
    "e2e",
}


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(script), *args], capture_output=True, text=True, check=False
    )


def _stages(gate: str) -> list[str]:
    out = _run(GATE_SCRIPTS[gate], "--stages")
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def _zones(gate: str) -> str:
    out = _run(GATE_SCRIPTS[gate], "--zones")
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _listed() -> dict[str, str]:
    """`stage -> description` from `gates.sh list`."""
    out = _run(GATES, "list")
    assert out.returncode == 0, out.stderr
    rows = {}
    for line in out.stdout.splitlines():
        stage, desc = (cell.strip() for cell in line.split("|", 1))
        rows[stage] = desc
    return rows


def _functions() -> set[str]:
    """What the dispatcher itself lists when refused an unknown stage."""
    out = _run(GATES, "no-such-stage")
    text = out.stdout + out.stderr
    assert "stages:" in text, text
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("stages:"))
    return set(line.split(":", 1)[1].split())


def test_every_gate_script_exists_and_declares_stages():
    for gate, script in GATE_SCRIPTS.items():
        assert script.is_file(), f"{gate} has no script at {script}"
        assert _stages(gate), f"{gate} declares no stage"


def test_every_listed_stage_is_a_function_and_every_function_is_listed():
    listed, functions = set(_listed()), _functions()

    assert listed == functions, {
        "listed but no ci_* function": sorted(listed - functions),
        "ci_* function but no row in `gates.sh list`": sorted(functions - listed),
    }


def test_every_row_carries_a_description():
    for stage, desc in _listed().items():
        assert desc, f"{stage} has no description — the ADR's stage table renders from it"


def test_every_stage_a_gate_requires_exists():
    known = _functions()
    for gate in GATE_SCRIPTS:
        phantom = set(_stages(gate)) - known
        assert not phantom, f"{gate} requires stages the runner does not have: {sorted(phantom)}"


def test_every_stage_is_required_by_a_gate_or_deliberately_not():
    """The "named nowhere" refusal: a new `ci_*` function must join a gate or the
    list above — a stage nobody decided about is how seven went missing."""
    required = {stage for gate in GATE_SCRIPTS for stage in _stages(gate)}

    undecided = _functions() - required - KNOWN_UNGATED
    stale = KNOWN_UNGATED & required

    assert not undecided, f"stages no gate requires and nobody exempted: {sorted(undecided)}"
    assert not stale, f"KNOWN_UNGATED lists stages a gate now requires: {sorted(stale)}"


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
    """Shrinking a set is the silent direction: markers on disk stay valid.

    `e2e-default` is the tier no zone claims — an UNEXPECTED regression, which is
    the breakage two independently green branches make together, and so belongs
    on the edge where a supervisor is present (ADR-0023 D3). The zones a change
    does touch are demanded earlier, by the diff, at `->merge`."""
    assert set(_stages("done-gate")) - set(_stages("merge-gate")) == {
        "integration",
        "master-ci",
        "merge-smoke",
        "e2e-default",
    }


def test_only_the_edges_before_the_merge_demand_the_zones_a_diff_names():
    """A zone is what THIS change is expected to break, and the agent fixes that
    alone at `->merge`. `->done` asks the other question — what the merge itself
    broke — and its tree is not the one that earned the stage, so asking there
    runs the same tests a second time for a refusal that edge is not for
    (ADR-0023 D11). `push-gate` requires the whole partition by name, decided by
    no diff.

    Every gate answers, including the git one: the renderer asks all of them, and
    an unknown flag there falls into check mode, which waits on stdin forever."""
    assert {gate: _zones(gate) for gate in GATE_SCRIPTS} == {
        "review-gate": "diff",
        "merge-gate": "diff",
        "done-gate": "none",
        "push-gate": "none",
    }


def test_a_precondition_is_never_part_of_a_verdict():
    """`prepare` asserts nothing; a gate naming it would count a venv build as
    evidence. Every gate there is, not a hand-kept three."""
    for gate in GATE_SCRIPTS:
        assert "prepare" not in _stages(gate), f"{gate} names a precondition as a stage"


def test_a_gate_is_not_a_stage():
    """`gates.sh done-gate` names nothing the runner has — the gate is its own
    script, and the runner knows no gate."""
    for gate in GATE_SCRIPTS:
        asked = _run(GATES, gate)
        assert asked.returncode == 2, asked.stderr
        assert "unknown stage" in asked.stderr


@pytest.mark.parametrize(
    "argv", [("check",), ("run",), ("check", "unit", "-k", "x"), ("unit", "x")]
)
def test_usage_errors_exit_64(argv: tuple[str, ...]):
    out = _run(GATES, *argv)

    assert out.returncode == 64, (argv, out.stderr)


def test_the_pytest_stages_are_exactly_the_functions_that_invoke_pytest():
    """`run` exports PYTEST_ADDOPTS (and says so) before the first stage in this
    list that runs, so a checker-only run stays silent about xdist. The list is
    hand-kept beside the functions; this is what keeps it honest."""
    src = GATES.read_text()
    declared = re.search(r"^PYTEST_STAGES='([^']*)'$", src, re.M)
    assert declared, "no PYTEST_STAGES line in gates.sh"
    bodies = re.findall(r"^ci_(\w+)\(\) \{\n(.*?)^\}", src, re.S | re.M)
    assert bodies, "no ci_* function parsed"
    invoking = {name.replace("_", "-") for name, body in bodies if "pytest" in body}

    assert set(declared.group(1).split()) == invoking
