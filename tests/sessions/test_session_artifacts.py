"""Tests for session_artifacts core and ClaudeSurface builder integration (HATS-1170)."""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
from pathlib import Path

from ai_hats_core import CompositionResult
from ai_hats.session_artifacts import (
    ArtifactCategory,
    BuiltArtifacts,
    DeliveryMode,
    SessionPolicy,
)
from ai_hats.surfaces.claude.provider import ClaudeSurface


def test_session_artifacts_types():
    assert ArtifactCategory.CONTEXT.value == "context"
    assert ArtifactCategory.SKILLS.value == "skills"
    assert ArtifactCategory.HOOKS.value == "hooks"

    assert DeliveryMode.CACHE_FLAG.value == "cache_flag"
    assert DeliveryMode.SDK_OPTION.value == "sdk_option"
    assert DeliveryMode.NATIVE_ROOT.value == "native_root"

    policy = SessionPolicy()
    assert policy.context is True
    assert policy.hooks is True

    artifacts = BuiltArtifacts()
    assert artifacts.cli_args == []
    assert artifacts.extra_env == {}
    assert artifacts.sdk_options == {}
    assert artifacts.materialized == []
    assert artifacts.full_content is None


def test_claude_build_session_artifacts_hitl(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    provider = ClaudeSurface()
    result = CompositionResult(name="test-role", priorities=[], rules=[], skills=[], injections=[])
    session_id = "20260724-120000-1"

    artifacts = provider.build_session_artifacts(
        ProjectLayout.at(project_dir),
        result,
        session_id,
        run_mode="hitl",
        artifacts=BuiltArtifacts(),
    )

    # CLI args assertion
    assert "--system-prompt-file" in artifacts.cli_args
    assert "--plugin-dir" in artifacts.cli_args
    assert "--settings" in artifacts.cli_args

    # Check cache settings file
    settings_idx = artifacts.cli_args.index("--settings") + 1
    settings_path = Path(artifacts.cli_args[settings_idx])
    assert settings_path.exists()
    settings_data = json.loads(settings_path.read_text())
    # Empty for a skill-less composition since HATS-1268 — every entry is
    # skill-declared. The wiring contract lives in
    # tests/test_provider_pretool_hook.py; this test owns the ADR-0018 seam.
    assert settings_data == {"hooks": {}}

    # Check clean-root invariant
    assert not (project_dir / "CLAUDE.md").exists()
    assert not (project_dir / ".claude" / "settings.json").exists()


def test_claude_build_session_artifacts_automate(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    provider = ClaudeSurface()
    result = CompositionResult(name="test-role", priorities=[], rules=[], skills=[], injections=[])
    session_id = "20260724-120000-1"

    artifacts = provider.build_session_artifacts(
        ProjectLayout.at(project_dir),
        result,
        session_id,
        run_mode="automate",
        artifacts=BuiltArtifacts(),
    )

    # HATS-1207 S3: AUTOMATE emits the SDK's preset+append shape — the same value
    # sdk_options.py used to recompute — rather than the marker-wrapped HITL bytes.
    sys_prompt = artifacts.sdk_options["system_prompt"]
    assert sys_prompt["type"] == "preset"
    assert sys_prompt["preset"] == "claude_code"
    assert "append" in sys_prompt
    assert "settings" in artifacts.sdk_options
    assert artifacts.sdk_options["setting_sources"] == []

    # Clean root check
    assert not (project_dir / "CLAUDE.md").exists()
    assert not (project_dir / ".claude" / "settings.json").exists()


def test_claude_session_policy_hooks_disabled(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    provider = ClaudeSurface()
    result = CompositionResult(name="test-role", priorities=[], rules=[], skills=[], injections=[])
    session_id = "20260724-120000-1"

    policy = SessionPolicy(hooks=False)
    artifacts = provider.build_session_artifacts(
        ProjectLayout.at(project_dir),
        result,
        session_id,
        run_mode="hitl",
        policy=policy,
        artifacts=BuiltArtifacts(),
    )

    assert "--settings" not in artifacts.cli_args


def test_clean_root_scaffold_disabled(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    provider = ClaudeSurface()

    provider.ensure_runtime_hooks(ProjectLayout.at(project_dir))
    assert not (project_dir / "CLAUDE.md").exists()
    assert not (project_dir / ".claude" / "settings.json").exists()
    assert provider.runtime_wiring_changes(ProjectLayout.at(project_dir)) == []
