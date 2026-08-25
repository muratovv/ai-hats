"""Contract tests for the OpenCode surface (HATS-1788)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats_opencode import OpenCodeProvider
from ai_hats_opencode.provider import AGENT_NAME, ENV_OPENCODE_CONFIG


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))


def _make_skill(tmp_path: Path, name: str, description: str = "Test skill") -> Path:
    source = tmp_path / "skill-sources" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nInstructions.\n"
    )
    return source


def _make_rule(tmp_path: Path, name: str = "safe-edits") -> SimpleNamespace:
    source = tmp_path / "rule-sources" / name
    source.mkdir(parents=True)
    (source / "rule.md").write_text("Never bypass repository safety checks.\n")
    return SimpleNamespace(name=name, source_path=source)


def _fake_result(
    *, skills: list[Path] | None = None, rules: list[SimpleNamespace] | None = None
) -> SimpleNamespace:
    skill_objs = [SimpleNamespace(name=path.name, source_path=path) for path in skills or []]
    return SimpleNamespace(
        name="maintainer",
        priorities=["Reliability"],
        merged_injection="## ROLE\nYou are the repository maintainer.",
        rules=rules or [],
        user_rules=(),
        skills=skill_objs,
        checks=(),
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def _session_id() -> str:
    return "20260822-000000-1-00000"


def _snapshot(project: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(project)): path.read_bytes()
        for path in sorted(project.rglob("*"))
        if path.is_file()
    }


def test_provider_identity_and_session_cache_contract(tmp_path: Path) -> None:
    provider = OpenCodeProvider()
    assert provider.name == "opencode"
    assert provider.detected_home_dirs() == [".opencode"]
    assert provider.system_prompt_path(tmp_path) is None
    assert provider.rules_dir(tmp_path / "session") == tmp_path / "session" / "rules"
    # Consent middleware is PATH-based and provider-agnostic; opencode opts in
    # so roles declaring consent points are enforceable on this surface.
    assert provider.supports_session_command_wrappers() is True


def test_context_hitl_materializes_session_agent(tmp_path: Path) -> None:
    provider = OpenCodeProvider()
    project = _project(tmp_path)
    result = _fake_result(
        skills=[_make_skill(tmp_path, "hatrack", "Drive the backlog")],
        rules=[_make_rule(tmp_path)],
    )
    artifacts = BuiltArtifacts()

    provider.build_session_artifacts(
        project, result, _session_id(), run_mode=RunMode.HITL, artifacts=artifacts
    )

    config_path = provider.session_config_path(project, _session_id())
    assert artifacts.extra_env[ENV_OPENCODE_CONFIG] == str(config_path)
    assert config_path.is_file(), "session config must be materialized"

    config = json.loads(config_path.read_text())
    agent = config["agent"][AGENT_NAME]
    assert agent["mode"] == "primary"
    assert "repository maintainer" in agent["prompt"]
    assert "## RULES" in agent["prompt"]
    assert "hatrack" in agent["prompt"], "skill index with cache paths must ride the prompt"
    assert str(provider.session_skills_root(project, _session_id())) in agent["prompt"]

    assert artifacts.cli_args[-2:] == ["--agent", AGENT_NAME]
    assert artifacts.full_content == agent["prompt"]


def test_context_config_carries_no_permission_keys(tmp_path: Path) -> None:
    """HATS-1792: work policy lives in the role's manifest, not the config."""
    provider = OpenCodeProvider()
    project = _project(tmp_path)

    provider.build_session_artifacts(
        project,
        _fake_result(skills=[_make_skill(tmp_path, "hatrack")]),
        _session_id(),
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    config = json.loads(provider.session_config_path(project, _session_id()).read_text())
    assert "permission" not in config, "generated config must stay free of policy keys"
    assert set(config) == {"$schema", "agent", "plugin"}


def test_hitl_build_writes_nothing_into_project_root(tmp_path: Path) -> None:
    provider = OpenCodeProvider()
    project = _project(tmp_path)
    before = _snapshot(project)

    provider.build_session_artifacts(
        project,
        _fake_result(skills=[_make_skill(tmp_path, "hatrack")]),
        _session_id(),
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    assert _snapshot(project) == before, "clean-root invariant violated"


def test_skills_mirror_lands_in_session_cache_with_path_env(tmp_path: Path) -> None:
    provider = OpenCodeProvider()
    project = _project(tmp_path)
    skill_source = _make_skill(tmp_path, "hatrack")
    (skill_source / "scripts").mkdir()
    (skill_source / "scripts" / "tool.sh").write_text("#!/bin/sh\ntrue\n")
    result = _fake_result(skills=[skill_source])
    artifacts = BuiltArtifacts()

    provider.build_session_artifacts(
        project, result, _session_id(), run_mode=RunMode.HITL, artifacts=artifacts
    )

    mirror = provider.session_skills_root(project, _session_id()) / "hatrack" / "SKILL.md"
    assert mirror.is_file()
    parts = artifacts.extra_env["PATH"].split(":")
    mirrored_scripts = str(
        provider.session_skills_root(project, _session_id()) / "hatrack" / "scripts"
    )
    assert mirrored_scripts in parts
    assert parts.index(mirrored_scripts) < parts.index(str(skill_source / "scripts")), (
        "mirror must precede source: PATH resolution follows composition order"
    )


def test_skills_mirror_is_natively_discoverable_via_xdg(tmp_path: Path) -> None:
    """HATS-1791: the mirror lives under the redirected config dir."""
    provider = OpenCodeProvider()
    project = _project(tmp_path)
    result = _fake_result(skills=[_make_skill(tmp_path, "hatrack")])
    artifacts = BuiltArtifacts()

    provider.build_session_artifacts(
        project, result, _session_id(), run_mode=RunMode.HITL, artifacts=artifacts
    )

    xdg_root = provider.session_xdg_config_home(project, _session_id())
    assert (xdg_root / "opencode" / "skills" / "hatrack" / "SKILL.md").is_file(), (
        "mirror must sit on a native discovery path"
    )
    assert artifacts.extra_env["XDG_CONFIG_HOME"] == str(xdg_root)


def test_base_config_home_is_projected_not_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HATS-1791: user-owned entries stay reachable via symlinks, never written."""
    base = tmp_path / "base-config"
    base_opencode = base / "opencode"
    (base_opencode / "skills" / "user-own-skill").mkdir(parents=True)
    (base_opencode / "skills" / "user-own-skill" / "SKILL.md").write_text(
        "---\nname: user-own-skill\ndescription: x\n---\nbody\n"
    )
    (base_opencode / "opencode.jsonc").write_text('{"$schema": "https://opencode.ai/config.json"}')
    (base_opencode / "plugins").mkdir()
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(base))

    provider = OpenCodeProvider()
    project = _project(tmp_path)
    composed = _make_skill(tmp_path, "hatrack")

    provider.build_session_artifacts(
        project,
        _fake_result(skills=[composed]),
        _session_id(),
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    session_dir = provider.session_xdg_config_home(project, _session_id()) / "opencode"
    assert (session_dir / "opencode.jsonc").is_symlink()
    assert (session_dir / "plugins").is_symlink()
    assert not (session_dir / "skills").is_symlink(), "skills dir is session-owned"

    skills_dir = provider.session_skills_root(project, _session_id())
    assert (skills_dir / "hatrack" / "SKILL.md").is_file(), "composed mirror is real files"
    assert (skills_dir / "user-own-skill").is_symlink(), "non-shadowed user skills projected"
    assert (base_opencode / "opencode.jsonc").read_text().startswith("{"), "base untouched"


def test_composed_skill_shadows_same_named_user_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base-config"
    base_skill = base / "opencode" / "skills" / "hatrack"
    base_skill.mkdir(parents=True)
    (base_skill / "SKILL.md").write_text("---\nname: hatrack\ndescription: user\n---\nuser\n")
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(base))

    provider = OpenCodeProvider()
    project = _project(tmp_path)
    composed = _make_skill(tmp_path, "hatrack", "composed wins")

    provider.build_session_artifacts(
        project,
        _fake_result(skills=[composed]),
        _session_id(),
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    skills_dir = provider.session_skills_root(project, _session_id())
    assert not (skills_dir / "hatrack").is_symlink()
    body = (skills_dir / "hatrack" / "SKILL.md").read_text()
    assert "composed wins" in body


def test_skillsless_role_pins_no_xdg(tmp_path: Path) -> None:
    provider = OpenCodeProvider()
    project = _project(tmp_path)
    artifacts = BuiltArtifacts()

    provider.build_session_artifacts(
        project,
        _fake_result(skills=[]),
        _session_id(),
        run_mode=RunMode.HITL,
        artifacts=artifacts,
    )

    assert "XDG_CONFIG_HOME" not in artifacts.extra_env


def test_get_env_pins_framework_identity(tmp_path: Path) -> None:
    from ai_hats.env import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR

    provider = OpenCodeProvider()
    env = provider.get_env(tmp_path / "session", tmp_path)
    assert env[ENV_AI_HATS_DIR].endswith(".agent/ai-hats") or "ai-hats" in env[ENV_AI_HATS_DIR]
    assert env[AI_HATS_PROJECT_DIR_ENV] == str(tmp_path)


def test_get_run_command_inserts_headless_run_subcommand() -> None:
    provider = OpenCodeProvider()
    command = provider.get_run_command(["opencode", "--agent", AGENT_NAME], "do the task")
    assert command == ["opencode", "run", "--agent", AGENT_NAME, "do the task"]


def test_passthrough_rejects_dangerous_and_owned_flags() -> None:
    provider = OpenCodeProvider()
    assert provider.get_cli_command(["--mini"]) == ["opencode", "--mini"]
    with pytest.raises(ValueError, match="--auto"):
        provider.get_cli_command(["--auto"])
    with pytest.raises(ValueError, match="--agent"):
        provider.get_cli_command(["--agent", "other"])


def test_model_flags_use_opencode_format() -> None:
    provider = OpenCodeProvider()
    assert provider.model_flags("openai/gpt-5") == ["--model", "openai/gpt-5"]
