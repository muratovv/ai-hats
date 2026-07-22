"""AgyProvider skills + prompt-channel tests (HATS-993)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG, gemini_md
from ai_hats.skills_dir import MANAGED_MARKER
from ai_hats_agy.provider import AgyProvider


@pytest.fixture
def agy_project(tmp_path):
    """Minimal library + role composed for the agy provider."""
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

    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(
        project / PROJECT_CONFIG
    )
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = asm.composer.compose("test-role")
    return project, result


def test_wrap_materializes_skills_into_agy_skills_dir(agy_project) -> None:
    project, result = agy_project
    provider = AgyProvider()

    provider.build_session_prompt(project, result, "sid-1")

    skills_dir = project / ".agy" / "skills"
    assert (skills_dir / "s" / "SKILL.md").is_file()
    refs = json.loads((skills_dir / MANAGED_MARKER).read_text())
    assert refs == {"sid-1": ["s"]}


def test_automate_hook_materializes_and_returns_no_args(agy_project) -> None:
    project, result = agy_project
    provider = AgyProvider()

    args = provider.materialize_runtime_skills(project, result, "sid-2")

    assert args == []
    assert (project / ".agy" / "skills" / "s" / "SKILL.md").is_file()


def test_system_prompt_omits_skills_index(agy_project) -> None:
    _, result = agy_project

    prompt = AgyProvider().build_system_prompt(result)

    # HATS-993: skills reach agy via the native .agy/skills/ registry;
    # the HATS-701 text-index is retired.
    assert "## AVAILABLE SKILLS" not in prompt


def test_agy_skills_dir_gitignored(agy_project) -> None:
    project, result = agy_project
    provider = AgyProvider()

    provider.materialize_runtime_skills(project, result, "sid-3")

    lines = (project / ".gitignore").read_text().splitlines()
    assert ".agy/skills/" in lines


def test_wrap_prompt_channel_is_add_dir(agy_project) -> None:
    project, result = agy_project

    args, env, prompt = AgyProvider().build_session_prompt(project, result, "sid-4")

    # HATS-993: GEMINI_CLI_PROJECT_RULES_PATH is dead in gemini-cli >=0.45;
    # the session role rides a GEMINI.md inside an --add-dir dir for agy.
    assert args[0] == "--add-dir"
    session_md = Path(args[1]) / "GEMINI.md"
    assert session_md.read_text() == prompt
    assert env == {}


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
    # Headless agy doesn't need --skip-trust.
    cmd = AgyProvider().get_run_command(["agy"], "do it")

    assert "-p" in cmd
    assert cmd[-1] == "do it"


def test_execution_context_temporarily_hides_root_gemini_and_agents_md(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    gemini = project / "GEMINI.md"
    agents = project / "AGENTS.md"
    gemini.write_text("root gemini rules")
    agents.write_text("root agents rules")

    provider = AgyProvider()
    with provider.execution_context(project):
        assert not gemini.exists()
        assert not agents.exists()
        assert any(p.name.startswith(".GEMINI.md.ai_hats_bak_") for p in project.iterdir())
        assert any(p.name.startswith(".AGENTS.md.ai_hats_bak_") for p in project.iterdir())

    assert gemini.is_file()
    assert agents.is_file()
    assert gemini.read_text() == "root gemini rules"
    assert agents.read_text() == "root agents rules"
    assert not any(p.name.startswith(".GEMINI.md.ai_hats_bak_") for p in project.iterdir())
    assert not any(p.name.startswith(".AGENTS.md.ai_hats_bak_") for p in project.iterdir())


def test_provider_name() -> None:
    assert AgyProvider().name == "agy"


def test_system_prompt_path(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert AgyProvider().system_prompt_path(project) == gemini_md(project)


def test_execution_context_cleans_up_recreated_target(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    gemini = project / "GEMINI.md"
    gemini.write_text("original")

    provider = AgyProvider()
    with provider.execution_context(project):
        # Target gets re-created while context is active
        gemini.write_text("recreated")

    assert gemini.is_file()
    assert gemini.read_text() == "original"


def test_rules_dir(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    assert AgyProvider().rules_dir(session_dir) == session_dir / "rules"


def test_get_cli_command() -> None:
    provider = AgyProvider()
    assert provider.get_cli_command() == ["agy"]
    assert provider.get_cli_command(["--foo", "bar"]) == ["agy", "--foo", "bar"]


def test_get_run_command_with_model() -> None:
    cmd = AgyProvider().get_run_command(["agy"], "task prompt", model="gemini-2.5-pro")
    assert cmd == ["agy", "--model", "gemini-2.5-pro", "-p", "task prompt"]


def test_get_env(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    session_dir = tmp_path / "session"
    env = AgyProvider().get_env(session_dir, project)
    assert env["AI_HATS_PROJECT_DIR"] == str(project)
    assert env["AI_HATS_DIR"] == str(project / ".agent" / "ai-hats")


def test_materializes_worktree_isolation_wt_gate_hook(tmp_path: Path) -> None:
    repo_root = Path(__file__).parent.parent.parent.parent.parent
    asm = Assembler(repo_root)
    result = asm.composer.compose("maintainer")

    project = tmp_path / "project"
    project.mkdir()
    provider = AgyProvider()
    provider.materialize_runtime_skills(project, result, "sid-wt")

    wt_skill_dir = project / ".agy" / "skills" / "worktree-isolation"
    assert (wt_skill_dir / "SKILL.md").is_file()
    assert (wt_skill_dir / "hooks" / "wt_gate.py").is_file()
    assert (wt_skill_dir / "hooks" / "code_extensions.json").is_file()


def test_ensure_runtime_hooks_writes_gemini_settings(tmp_path: Path) -> None:
    repo_root = Path(__file__).parent.parent.parent.parent.parent
    asm = Assembler(repo_root)
    result = asm.composer.compose("maintainer")

    project = tmp_path / "project"
    project.mkdir()
    provider = AgyProvider()
    provider.ensure_runtime_hooks(project, result)

    settings_file = project / ".gemini" / "settings.json"
    assert settings_file.is_file(), ".gemini/settings.json must be created by ensure_runtime_hooks"
    data = json.loads(settings_file.read_text())
    pre_tool_hooks = data.get("hooks", {}).get("PreToolUse", [])
    assert any("wt_gate.py" in str(h) for h in pre_tool_hooks)


def test_build_session_prompt_materializes_runtime_hooks_and_settings(tmp_path: Path) -> None:
    repo_root = Path(__file__).parent.parent.parent.parent.parent
    asm = Assembler(repo_root)
    result = asm.composer.compose("maintainer")

    project = tmp_path / "project"
    project.mkdir()
    provider = AgyProvider()

    provider.build_session_prompt(project, result, "sid-sp-settings")

    settings_file = project / ".gemini" / "settings.json"
    assert settings_file.is_file(), ".gemini/settings.json must be created during build_session_prompt"
    data = json.loads(settings_file.read_text())
    pre_tool_hooks = data.get("hooks", {}).get("PreToolUse", [])
    assert any("wt_gate.py" in str(h) for h in pre_tool_hooks)









