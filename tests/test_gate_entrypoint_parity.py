"""Every gate runs through one entry point, so local and CI cannot drift apart.

HATS-725/1372: `scripts/gates.sh` is the single source of the check commands.
A Makefile target or CI job that spells out `ruff`/`pytest`/`bandit` itself is
free to disagree with it — which is how `make lint` came to check a narrower
path set than CI, and how the formatter check ended up running in neither.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# Commands that decide pass/fail for a gate. Spelling one of these out anywhere
# but gates.sh forks the definition of "the gate".
_RUNNERS = (
    "ruff",
    "pytest",
    "bandit",
    "pip_audit",
    "pip-audit",
    "check_pkg_version_skew.py",
    # HATS-1373: check_dependency_floor.py was absent here since HATS-1399 —
    # a gate script this ratchet was silently not ratcheting.
    "check_dependency_floor.py",
    "check_silent_fallback.py",
    "check_python_pin.py",
    "check_test_isolation.py",
    "gen_e2e_catalog.py",
)

# The two sanctioned entry points: the CI stage dispatcher and the thin e2e
# wrapper that delegates to the pre-push gate's run mode.
_ENTRY_POINTS = ("scripts/gates.sh", "scripts/run-e2e-gate.sh")

_RUNNER_RE = re.compile(
    r"(?:^|[\s;&|(=/])(?:python[\d.]*\s+-m\s+)?(" + "|".join(map(re.escape, _RUNNERS)) + r")\b"
)
_INSTALL_RE = re.compile(r"\b(?:pip[\d.]*|uv)\s+(?:pip\s+)?install\b")

# A stage named by a caller: `bash scripts/gates.sh <stage>` in ci.yml, or the
# Makefile's `$(CI_LOCAL) <stage>`. `--stages <gate>` asks a different question.
_STAGE_CALL_RE = re.compile(
    r"(?:bash\s+scripts/gates\.sh|\$\(CI_LOCAL\))\s+(?!--)([a-z0-9][a-z0-9-]*)"
)


def raw_gate_invocations(script: str) -> list[str]:
    """Command lines that run a check themselves instead of delegating."""
    offenders = []
    for raw in script.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or _INSTALL_RE.search(line):
            continue
        if any(entry in line for entry in _ENTRY_POINTS):
            continue
        if _RUNNER_RE.search(line):
            offenders.append(line)
    return offenders


def makefile_recipes(makefile: str) -> str:
    """Only the recipe lines — a Makefile's commands all start with a tab."""
    return "".join(line for line in makefile.splitlines(keepends=True) if line.startswith("\t"))


def workflow_run_steps(workflow: str) -> str:
    """Every `run:` body in a GitHub workflow, concatenated.

    Step `name:` fields describe a gate ("lint (ruff)") without running it, so
    scanning raw YAML text would flag prose.
    """
    scripts = []
    for job in (yaml.safe_load(workflow).get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if "run" in step:
                scripts.append(str(step["run"]))
    return "\n".join(scripts)


def test_inline_pytest_in_a_recipe_is_flagged():
    assert raw_gate_invocations("\tpytest tests/ -q\n") == ["pytest tests/ -q"]


def test_inline_ruff_is_flagged():
    assert raw_gate_invocations("ruff check .\n") == ["ruff check ."]


def test_python_dash_m_form_is_flagged():
    assert raw_gate_invocations("python3 -m bandit -r src/\n") == ["python3 -m bandit -r src/"]


def test_delegation_to_the_dispatcher_passes():
    assert raw_gate_invocations("bash scripts/gates.sh lint\n") == []


def test_installing_a_runner_is_not_invoking_it():
    assert raw_gate_invocations('pip install "ruff>=0.4"\n') == []


def test_a_comment_naming_a_runner_is_not_an_invocation():
    assert raw_gate_invocations("# unit tests run under pytest, ~148s\n") == []


def test_makefile_recipes_drop_target_and_variable_lines():
    makefile = 'TIMEOUT ?= 300\nunit:\n\tpytest tests/\n\nhelp: ## show\n\t@awk "..."\n'
    assert makefile_recipes(makefile) == '\tpytest tests/\n\t@awk "..."\n'


def test_workflow_run_steps_ignores_step_names():
    workflow = """
jobs:
  lint:
    steps:
      - name: ruff check
        run: bash scripts/gates.sh lint
"""
    assert workflow_run_steps(workflow) == "bash scripts/gates.sh lint"


def test_makefile_delegates_every_gate():
    offenders = raw_gate_invocations(makefile_recipes((REPO_ROOT / "Makefile").read_text()))
    assert not offenders, (
        "Makefile spells out gate commands instead of delegating to "
        f"scripts/gates.sh — they will drift from CI: {offenders}"
    )


def test_ci_workflow_delegates_every_gate():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    offenders = raw_gate_invocations(workflow_run_steps(workflow))
    assert not offenders, (
        "ci.yml runs a gate command directly instead of calling a "
        f"scripts/gates.sh stage: {offenders}"
    )


def _known_stages() -> set[str]:
    """The dispatcher's own answer: it prints every stage it knows on an unknown one."""
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(REPO_ROOT / "scripts" / "gates.sh"), "no-such-stage"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 2, out.stderr
    line = next(ln for ln in out.stderr.splitlines() if ln.strip().startswith("stages:"))
    return set(line.split(":", 1)[1].split())


def test_every_caller_names_a_stage_the_dispatcher_knows():
    """HATS-1716: the stage set is the set of `ci_*` functions, so renaming one
    silently unwires every caller that spells the old name."""
    called = set(
        _STAGE_CALL_RE.findall(
            workflow_run_steps((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
            + "\n"
            + makefile_recipes((REPO_ROOT / "Makefile").read_text())
        )
    )
    unknown = sorted(called - _known_stages() - {"all"})
    assert not unknown, (
        "ci.yml / Makefile call a gates.sh stage the dispatcher does not "
        f"know — that job runs nothing and exits 2: {unknown}"
    )


_GATE_HOOKS = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate"
    / "hooks"
)


def _composition(gate: str) -> list[str]:
    """What the gate script itself says it requires — never a literal here."""
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(_GATE_HOOKS / f"{gate}.sh"), "--stages"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def test_the_done_gate_demands_what_only_it_can_ask():
    """ADR-0023 D4 splits the edges by question, and the split is REAL only
    while `->done` carries stages `->merge` does not. Shrinking a set is the
    silent direction: markers already on disk stay valid (HATS-1601). The
    nesting itself is `tests/test_gates_table.py`'s."""
    merge, done = set(_composition("merge-gate")), set(_composition("done-gate"))

    assert done - merge == {"integration", "master-ci", "merge-smoke"}, (
        "`->done` asks whether master is green after this card; `integration` and "
        "`merge-smoke` are the stages that answer it, and `master-ci` (HATS-1877) "
        "asks the same question of the base the card is about to land on. Actual "
        f"extra: {sorted(done - merge)}"
    )


def test_every_gate_the_makefile_earns_is_a_gate_script_and_vice_versa():
    """`make <gate>` runs `hooks/<gate>.sh --run`; a target with no script is a
    door to nowhere, and a script with no target cannot be earned by hand. The
    roster is the hooks directory, so nothing here is a hand-kept list."""
    roster = {p.stem for p in _GATE_HOOKS.glob("*-gate.sh")}
    assert roster, "the skill ships no gate script at all"
    targets = set(
        re.findall(r"^\s*\$\(call run_gate,([a-z-]+)\)", (REPO_ROOT / "Makefile").read_text(), re.M)
    )
    assert targets, "the Makefile earns no gate at all"
    assert targets == roster, {
        "make target without a gate script": sorted(targets - roster),
        "gate script without a make target": sorted(roster - targets),
    }
