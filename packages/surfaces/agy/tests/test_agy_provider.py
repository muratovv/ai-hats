"""AgyProvider skills + prompt-channel tests (HATS-993, HATS-1166)."""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG, gemini_md, session_cache_dir
from ai_hats_agy.provider import AgyProvider


@pytest.fixture
def agy_project(tmp_path, monkeypatch):
    """Minimal library + role composed for the agy provider."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    skill_dir = lib / "skills" / "s"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: x\n---\n# body\n")

    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities:\n  - Quality\n"
        "composition:\n  skills: [s]\n"
        "injection: Role body.\n"
    )

    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = asm.composer.compose("test-role")
    return project, result


def test_wrap_materializes_skills_into_session_skills_dir(agy_project) -> None:
    project, result = agy_project
    provider = AgyProvider()

    provider.build_session_prompt(project, result, "sid-1")

    skills_dir = session_cache_dir(project, "sid-1") / "rules" / ".agents" / "skills"
    assert (skills_dir / "s" / "SKILL.md").is_file()


def test_automate_hook_materializes_and_returns_no_args(agy_project) -> None:
    project, result = agy_project
    provider = AgyProvider()

    args = provider.materialize_runtime_skills(project, result, "sid-2")

    assert args == []
    skills_dir = session_cache_dir(project, "sid-2") / "rules" / ".agents" / "skills"
    assert (skills_dir / "s" / "SKILL.md").is_file()


def test_system_prompt_omits_skills_index(agy_project) -> None:
    _, result = agy_project

    prompt = AgyProvider().build_system_prompt(result)

    assert "## AVAILABLE SKILLS" not in prompt


def test_wrap_prompt_channel_is_add_dir(agy_project) -> None:
    project, result = agy_project

    args, env, prompt = AgyProvider().build_session_prompt(project, result, "sid-4")

    assert args[0] == "--add-dir"
    session_md = Path(args[1]) / "GEMINI.md"
    assert session_md.read_text() == prompt
    # The only env the prompt channel carries: the dispatcher's cache-dir pin (HATS-1398).
    assert env == {"AI_HATS_SESSION_CACHE_DIR": str(session_cache_dir(project, "sid-4"))}


def test_wrap_session_dirs_isolated_per_session(agy_project) -> None:
    project, result = agy_project
    provider = AgyProvider()

    args_a, _, _ = provider.build_session_prompt(project, result, "sid-a")
    args_b, _, _ = provider.build_session_prompt(project, result, "sid-b")

    assert args_a[1] != args_b[1]


def test_get_env_carries_no_dead_rules_path(agy_project, tmp_path) -> None:
    project, _ = agy_project

    env = AgyProvider().get_env(tmp_path / "sess", project)

    assert "GEMINI_CLI_PROJECT_RULES_PATH" not in env


def test_get_run_command_headless_skips_trust() -> None:
    cmd = AgyProvider().get_run_command(["agy"], "do it")

    assert "-p" in cmd
    assert cmd[-1] == "do it"


def test_execution_context_is_clean_no_op_native_by_default(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    gemini = project / "GEMINI.md"
    agents = project / "AGENTS.md"
    gemini.write_text("root gemini rules")
    agents.write_text("root agents rules")

    provider = AgyProvider()
    with provider.execution_context(project):
        assert gemini.exists()
        assert agents.exists()
        assert not any(p.name.startswith(".GEMINI.md.ai_hats_bak_") for p in project.iterdir())

    assert gemini.is_file()
    assert agents.is_file()


def test_provider_name() -> None:
    assert AgyProvider().name == "agy"


def test_system_prompt_path(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert AgyProvider().system_prompt_path(project) == gemini_md(project)


def test_rules_dir(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    assert AgyProvider().rules_dir(session_dir) == session_dir / "rules"


def test_get_cli_command() -> None:
    provider = AgyProvider()
    assert provider.get_cli_command() == ["agy"]
    assert provider.get_cli_command(["--foo", "bar"]) == ["agy", "--foo", "bar"]


def test_get_cli_launch_args_translates_positional_prompt() -> None:
    provider = AgyProvider()
    base_cmd = ["agy", "hello world", "--add-dir", "/path/to/rules"]
    assert provider.get_cli_launch_args(base_cmd, "sid-1", False) == [
        "agy",
        "-i",
        "hello world",
        "--add-dir",
        "/path/to/rules",
    ]


def test_get_cli_launch_args_preserves_existing_prompt_flag() -> None:
    provider = AgyProvider()
    base_cmd = ["agy", "-i", "hello world", "--add-dir", "/path/to/rules"]
    assert provider.get_cli_launch_args(base_cmd, "sid-1", False) == base_cmd


def test_get_cli_launch_args_with_model_flag_and_positional_prompt() -> None:
    provider = AgyProvider()
    base_cmd = ["agy", "--model", "gemini-2.5-pro", "hello world", "--add-dir", "/path/to/rules"]
    assert provider.get_cli_launch_args(base_cmd, "sid-1", False) == [
        "agy",
        "-i",
        "hello world",
        "--model",
        "gemini-2.5-pro",
        "--add-dir",
        "/path/to/rules",
    ]


def test_get_run_command_with_harness_flags() -> None:
    provider = AgyProvider()
    flags = provider.model_flags("gemini-2.5-pro")
    cmd = provider.get_run_command(["agy"] + flags, "task prompt")
    assert cmd == [
        "agy",
        "--model",
        "gemini-2.5-pro",
        "--output-format",
        "json",
        "-p",
        "task prompt",
    ]


def test_get_env(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    session_dir = tmp_path / "session"
    env = AgyProvider().get_env(session_dir, project)
    assert env["AI_HATS_PROJECT_DIR"] == str(project)
    assert env["AI_HATS_DIR"] == str(project / ".agent" / "ai-hats")


def test_materializes_worktree_isolation_wt_gate_hook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    repo_root = Path(__file__).parent.parent.parent.parent.parent
    asm = Assembler(repo_root)
    result = asm.composer.compose("maintainer")

    project = tmp_path / "project"
    project.mkdir()
    provider = AgyProvider()
    provider.materialize_runtime_skills(project, result, "sid-wt")

    wt_skill_dir = (
        session_cache_dir(project, "sid-wt") / "rules" / ".agents" / "skills" / "worktree-isolation"
    )
    assert (wt_skill_dir / "SKILL.md").is_file()
    assert (wt_skill_dir / "hooks" / "wt_gate.py").is_file()


def test_build_session_prompt_materializes_hooks_manifest_in_cache_and_clean_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    repo_root = Path(__file__).parent.parent.parent.parent.parent
    asm = Assembler(repo_root)
    result = asm.composer.compose("maintainer")

    project = tmp_path / "project"
    project.mkdir()
    provider = AgyProvider()

    provider.build_session_prompt(project, result, "sid-sp-settings")

    # Clean-Root Invariant: project root .gemini/settings.json must NOT be written
    root_settings = project / ".gemini" / "settings.json"
    assert not root_settings.exists(), (
        "Clean-Root Invariant: .gemini/settings.json must not be created in project root"
    )

    # Session hooks manifest must be in session cache
    cache_hooks = session_cache_dir(project, "sid-sp-settings") / "hooks.json"
    assert cache_hooks.is_file()
    data = json.loads(cache_hooks.read_text())
    pre_tool_hooks = data.get("PreToolUse", [])
    assert any("wt_gate.py" in str(h.get("command")) for h in pre_tool_hooks)


def test_agy_provider_detected_home_dirs() -> None:
    provider = AgyProvider()
    assert ".gemini" in provider.detected_home_dirs()
    assert ".agy" in provider.detected_home_dirs()


def test_build_session_artifacts_automate_materializes_hooks_and_fires(
    tmp_path: Path, monkeypatch
) -> None:
    """HATS-1223: AUTOMATE mode writes hooks.json and global dispatcher fires session hook."""
    from ai_hats.session_artifacts import BuiltArtifacts, RunMode
    from ai_hats_agy.hook_dispatcher import dispatch_hook

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    marker = tmp_path / "hook_fired.txt"
    hook_script_name = "run_hook.sh"

    skill_dir = lib / "skills" / "hook-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: hook-skill\n"
        "description: skill with hook\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Edit\n"
        f"        script: {hook_script_name}\n"
        "---\n"
        "# body\n"
    )
    script_file = skill_dir / hook_script_name
    script_file.write_text(f"#!/bin/sh\necho 'FIRED' > '{marker}'\n")
    script_file.chmod(0o755)

    role_dir = lib / "roles" / "hook-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: hook-role\n"
        "priorities:\n  - Reliability\n"
        "composition:\n  skills: [hook-skill]\n"
        "injection: Hook role body.\n"
    )

    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = asm.composer.compose("hook-role")

    provider = AgyProvider()
    artifacts = BuiltArtifacts()
    provider.build_session_artifacts(
        project, result, "sid-auto", run_mode=RunMode.AUTOMATE, artifacts=artifacts
    )

    # 1. Manifest written in AUTOMATE session cache
    cache_hooks = session_cache_dir(project, "sid-auto") / "hooks.json"
    assert cache_hooks.is_file(), (
        "hooks.json must be materialized in session cache under AUTOMATE mode"
    )
    data = json.loads(cache_hooks.read_text())
    pre_tool_hooks = data.get("PreToolUse", [])
    assert any(hook_script_name in str(h.get("command")) for h in pre_tool_hooks)

    # 2. Context contains PRIORITIES and role body
    assert artifacts.full_content is not None
    assert "## PRIORITIES" in artifacts.full_content
    assert "Hook role body." in artifacts.full_content

    # 3. Acceptance proof: agy global dispatcher fires the session hook in AUTOMATE session.
    #    The env comes from the builder, so the pin the dispatcher reads is the one the
    #    session actually exports (HATS-1398) — not a value this test invented.
    from ai_hats.session_identity import SessionIdentity

    identity = SessionIdentity(
        id="sid-auto",
        role="hook-role",
        provider="agy",
        project_dir=project,
        session_dir=project / "session",
    )
    for key, value in identity.to_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(project))
    for key, value in artifacts.extra_env.items():
        monkeypatch.setenv(key, value)
    res = dispatch_hook("PreToolUse", tool_name="Edit")
    assert res == 0
    assert marker.is_file()
    assert marker.read_text().strip() == "FIRED"
