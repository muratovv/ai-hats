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


def test_the_gate_script_is_shipped_beside_the_hook_that_imports_it():
    """Green must mean 'checked and clean', never 'the file moved'."""
    assert GATE.is_file(), f"the hook's sibling is not where it imports it from: {GATE}"


HOOK = GATE.parent / "safety_gate.py"


def test_the_lint_is_carried_by_the_hook_not_by_a_bound_check():
    """Where it lives IS the design (HATS-1642).

    ADR-0019 D9 clause 4 will not resolve a check whose skill sits in a linked
    worktree — "a gate must not run the half-written copy of itself" — and
    ai-hats is developed from worktrees, so a bound row would refuse to start
    every such session. The hook runs everywhere; it carries the lint instead.
    """
    library = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
    declared = [
        path
        for path in sorted(library.rglob("config.yaml"))
        if "consent_permission_lint" in path.read_text(encoding="utf-8")
    ]
    assert declared == [], f"a bound row would refuse to resolve from a worktree: {declared}"
    assert "consent_permission_lint" in HOOK.read_text(encoding="utf-8"), (
        "the hook must carry the lint, or nothing runs it at all"
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
        # The harness's prefix idiom — this one silences the pre-merge pause.
        "Bash(ai-hats:*)",
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


def test_the_message_names_the_file_the_rule_and_the_pause_it_removes(lint, tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text(
        _settings(["Bash(rack transition *)", "Bash(ai-hats:*)"]), encoding="utf-8"
    )

    said = lint.warning_for(roots=[tmp_path])

    assert "settings.local.json" in said, said
    assert "rack transition" in said and "ai-hats:*" in said, said
    assert "consent question" in said and "merge into master" in said, said


def test_a_clean_project_says_nothing(lint, tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        _settings(["Bash(rack context *)"]), encoding="utf-8"
    )

    assert lint.warning_for(roots=[tmp_path]) == ""


def test_a_session_is_told_once(lint, tmp_path):
    marker = tmp_path / "consent-lint.json"

    assert lint.already_warned(marker, "sid-1") is False
    assert lint.already_warned(marker, "sid-1") is True
    assert lint.already_warned(marker, "sid-2") is False, "a new session hears it again"
