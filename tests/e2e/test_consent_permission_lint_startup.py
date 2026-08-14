"""e2e (HATS-1642)

flow:   a developer launching a session in a project whose permissions.allow
        auto-approves the very transition the consent gate exists to question
cmds:
    ai-hats
expect: session startup warns, naming the settings file and the offending rule,
        and the launch still proceeds
why:    the gate was measured switching off in silence — no prompt went up while
        the guard still injected its ticket, so the card moved regardless
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from _helpers.git import git as _git_helper

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_SKILL = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard"
#: The row trait-base ships (HATS-1642) — repeated here so the synthetic library
#: exercises the same shape the real one declares.
BINDING = {
    "run": "safety-guard/hooks/consent_permission_lint.py",
    "at": ["startup"],
    "on_error": "warn",
}


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """A real git project + a library whose trait binds the lint at startup."""
    project = tmp_path / "project"
    project.mkdir()
    _git_helper(project, "init", "--quiet")
    _git_helper(project, "config", "user.email", "t@e.com")
    _git_helper(project, "config", "user.name", "t")

    lib = tmp_path / "lib"
    # The REAL skill, so the bytes under test are the shipped ones.
    shutil.copytree(SHIPPED_SKILL, lib / "skills" / "safety-guard", symlinks=False)

    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - safety-guard\n"
        "  apps:\n    ai-hats:\n      - run: " + BINDING["run"] + "\n"
        "        at: [startup]\n        on_error: warn\ninjection: B.\n"
    )
    role = lib / "roles" / "gated-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: gated-role\npriorities: [Safety]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(
        "provider: claude\nai_hats_dir: .agent/ai-hats\n"
        "active_role: gated-role\ndefault_role: gated-role\n"
        "library_paths:\n  - " + str(lib) + "\n"
    )
    return project, lib


def _allow(project: Path, rules: list[str]) -> None:
    path = project / ".claude" / "settings.local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": {"allow": rules}}, indent=2))


def _launch(project: Path, monkeypatch) -> str:
    from ai_hats import runtime as rt

    monkeypatch.setattr(
        rt.WrapRunner,
        "_pty_spawn",
        lambda self, cmd, env, tracer, pty_tap_factory=None, on_spawn=None: 0,
    )
    monkeypatch.setenv("AI_HATS_STARTUP_HOLD", "0.05")
    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"the lint must never refuse a launch; exited {result.exit_code}\n"
        f"{result.output}\nexc={result.exception!r}"
    )
    return result.output


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    root, lib = _make_project(tmp_path)
    asm = Assembler(root, library_paths=[lib])
    asm.init()
    asm.set_role("gated-role", provider_name="claude")
    # The check reads the USER's settings too; pin HOME so the developer's own
    # allow-list cannot decide this test either way.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-cfg"))
    return root


def test_an_allow_rule_that_silences_the_consent_gate_is_reported_at_startup(project, monkeypatch):
    _allow(project, ["Bash(rack context *)", "Bash(rack transition *)"])

    output = _launch(project, monkeypatch)

    assert "rack transition" in output, output
    assert "settings.local.json" in output, output
    assert "startup warning" in output, output


def test_a_project_whose_rules_leave_the_gate_alone_says_nothing(project, monkeypatch):
    """Fail-under-revert: without this, the test above would pass on ANY warning
    the launch happens to print. Scoped to this check's own words, because a
    session carries unrelated notices (a stale dev env, for one)."""
    _allow(project, ["Bash(rack context *)", "Bash(git status:*)"])

    output = _launch(project, monkeypatch)

    assert "consent gate" not in output, output
    assert "rack transition" not in output, output
