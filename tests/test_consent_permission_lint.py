"""HATS-1642 — the consent gate must not switch off in silence.

The live probe caught `plan → execute` going through with no question asked: a
`"Bash(rack transition *)"` under `permissions.allow` made the harness
auto-approve the call, so the prompt never went up while the guard still
injected its ticket. An invariant that held only in prose now holds in code —
this pins the code.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard"
    / "hooks/consent_permission_lint.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("consent_permission_lint", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lint():
    return _load()


def test_the_gate_script_is_shipped_and_runnable():
    """Green must mean 'checked and clean', never 'the file moved'."""
    assert GATE.is_file(), f"the check script is not where the binding points: {GATE}"
    assert GATE.read_text(encoding="utf-8").startswith("#!"), "a bound check needs a shebang"


#: The row this check is meant to ship under, held here rather than in a trait
#: — see `test_the_row_is_not_declared_in_the_library_yet` for why, and
#: `tests/e2e/test_consent_permission_lint_startup.py`, which drives this exact
#: shape through a real launch.
BINDING = {
    "run": "safety-guard/hooks/consent_permission_lint.py",
    "at": ["startup"],
    "on_error": "warn",
}


def test_the_row_is_not_declared_in_the_library_yet():
    """Held back, deliberately, and pinned so the state is a decision not a slip.

    ADR-0019 D9 clause 4 refuses to resolve a check whose skill sits inside a
    LINKED WORKTREE — "a gate must not run the half-written copy of itself".
    Sound, but ai-hats is developed from worktrees, so a row on a universal
    trait makes every such session refuse to start (measured: 14 launch tests).
    Landing it is a supervisor's call, and one line.
    """
    import yaml

    library = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
    declared = [
        path
        for path in sorted(library.rglob("config.yaml"))
        if BINDING["run"] in path.read_text(encoding="utf-8")
    ]
    assert declared == [], f"the row landed without the worktree question settled: {declared}"

    base = yaml.safe_load(
        (library / "core/traits/trait-base/config.yaml").read_text(encoding="utf-8")
    )
    assert "safety-guard" in base["composition"]["skills"], (
        "trait-base is the intended home — it already carries the skill this check guards"
    )


def _settings(rules) -> str:
    return json.dumps({"permissions": {"allow": rules}}, indent=2)


@pytest.mark.parametrize(
    "rule",
    [
        "Bash(rack transition *)",
        "Bash(rack *)",
        "Bash(*)",
        "Bash",
        "Bash(rack transition HATS-1 execute)",
        # The form the guard refuses outright, fossilised into an allow-rule.
        "Bash(AI_HATS_PLAN_ACK=1 rack transition HATS-1193 execute)",
    ],
)
def test_a_rule_that_disarms_the_consent_gate_is_reported(lint, rule):
    found = lint.findings_in(_settings([rule]))
    assert len(found) == 1, f"{rule!r} was not reported: {found}"
    assert found[0].rule == rule


@pytest.mark.parametrize(
    "rule",
    [
        "Bash(rack context *)",
        "Bash(rack ls*)",
        "Bash(rack transition * --log *)",
        "Bash(git status:*)",
        "Read(//tmp/**)",
    ],
)
def test_a_harmless_rule_is_left_alone(lint, rule):
    """A lint that flags everything is as useless as one that flags nothing."""
    assert lint.findings_in(_settings([rule])) == []


def test_a_finding_names_the_line_it_sits_on(lint):
    text = _settings(["Bash(rack context *)", "Bash(rack transition *)"])
    found = lint.findings_in(text)

    assert len(found) == 1
    line = text.splitlines()[found[0].line - 1]
    assert "rack transition" in line, f"line {found[0].line} is not the offending one: {line!r}"


def test_unreadable_settings_are_not_a_finding(lint):
    """The check reports what it can read; malformed JSON is somebody else's gate."""
    assert lint.findings_in("{not json at all") == []
    assert lint.findings_in(json.dumps({"permissions": {}})) == []
    assert lint.findings_in(json.dumps([])) == []


def test_it_reports_and_never_refuses(lint, tmp_path, capsys):
    """`on_error: warn` softens a BROKE run (exit 1), never a REFUSE (exit 2) —
    so a reporting check must never reach for 2 (ADR-0019 D4)."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text(
        _settings(["Bash(rack transition *)"]), encoding="utf-8"
    )

    code = lint.main(roots=[tmp_path])

    assert code == 1, "a reporting check exits 1, so on_error: warn can soften it"
    said = capsys.readouterr().out
    assert "settings.local.json" in said and "rack transition" in said, said


def test_a_clean_project_says_nothing(lint, tmp_path, capsys):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        _settings(["Bash(rack context *)"]), encoding="utf-8"
    )

    assert lint.main(roots=[tmp_path]) == 0
    assert capsys.readouterr().out == ""
