"""Tests for session_artifacts core and the claude plan's delivery shape (HATS-1170)."""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
from pathlib import Path

from ai_hats_core import CompositionResult
from ai_hats.materialization import WriteKind
from ai_hats.session_artifacts import ArtifactCategory, RunMode, SessionPolicy
from ai_hats.surfaces.claude.provider import ClaudeSurface
from tests._plan_helpers import composition_of, planned


def test_session_artifacts_types():
    assert ArtifactCategory.CONTEXT.value == "context"
    assert ArtifactCategory.SKILLS.value == "skills"
    assert ArtifactCategory.HOOKS.value == "hooks"

    policy = SessionPolicy()
    assert policy.context is True
    assert policy.hooks is True


def _plan(project_dir: Path, run_mode: RunMode, policy: SessionPolicy | None = None):
    layout = ProjectLayout.at(project_dir)
    result = CompositionResult(name="test-role", priorities=[], rules=[], skills=[], injections=[])
    return planned(
        ClaudeSurface(),
        composition_of(result, layout=layout),
        layout=layout,
        root=layout.cache.session("20260724-120000-1"),
        run_mode=run_mode,
        policy=policy,
    )


def test_claude_plan_hitl(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    plan = _plan(project_dir, RunMode.HITL)
    args = list(plan.launch.args)

    # CLI args assertion
    assert "--system-prompt-file" in args
    assert "--plugin-dir" in args
    assert "--settings" in args

    # The settings document the plan writes
    settings_path = Path(args[args.index("--settings") + 1])
    entry = next(
        e for e in plan.entries if e.kind is WriteKind.WRITE_TEXT and e.target == settings_path
    )
    settings_data = json.loads(entry.content)
    # A skill-less composition wires no gate (every gate is skill-declared,
    # HATS-1268) — only the observer that lets the session's own record say
    # when claude is showing the person a permission prompt. The wiring
    # contract lives in tests/test_provider_pretool_hook.py.
    assert list(settings_data["hooks"]) == ["Notification"]

    # Check clean-root invariant: every target is under the session root
    assert all(e.target.is_relative_to(plan.root) for e in plan.entries)
    assert not (project_dir / "CLAUDE.md").exists()
    assert not (project_dir / ".claude" / "settings.json").exists()


def test_claude_plan_automate(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    plan = _plan(project_dir, RunMode.AUTOMATE)

    # HATS-1207 S3: AUTOMATE emits the SDK's preset+append shape rather than
    # the marker-wrapped HITL bytes.
    options = plan.launch.sdk_options
    sys_prompt = options["system_prompt"]
    assert sys_prompt["type"] == "preset"
    assert sys_prompt["preset"] == "claude_code"
    assert "append" in sys_prompt
    assert "settings" in options
    assert options["setting_sources"] == []

    # Clean root check
    assert not (project_dir / "CLAUDE.md").exists()
    assert not (project_dir / ".claude" / "settings.json").exists()


def test_claude_session_policy_hooks_disabled(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    plan = _plan(project_dir, RunMode.HITL, SessionPolicy(hooks=False))

    assert "--settings" not in plan.launch.args
    assert not any(e.target.name == "settings.json" for e in plan.entries)
