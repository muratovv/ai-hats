"""HATS-307: SubAgentRunner must pass spawned role's skills via --plugin-dir.

Without this, `ai-hats reflect role <X>` spawns a sub-agent whose Skill registry
still reflects the project's active_role, so role-specific skills surface as
`Unknown skill`. This test mocks `subprocess.run` and asserts the spawned `claude`
command contains `--plugin-dir <tmp>` and that the dir is removed afterwards.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.providers import ClaudeProvider, GeminiProvider


@pytest.fixture
def project_with_two_roles(tmp_path: Path) -> tuple[Path, Path]:
    """Project with: active_role 'host' (no skills) + spawnable 'guest' (1 skill)."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    # A skill unique to 'guest'.
    skill_dir = lib / "skills" / "guest-only-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: guest-only-skill\ndescription: x\n---\n# guest body\n"
    )

    # Minimal trait
    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text("name: trait-base\ninjection: Base.\n")

    # host role — no skills
    host_dir = lib / "roles" / "host"
    host_dir.mkdir(parents=True)
    (host_dir / "config.yaml").write_text(
        "name: host\npriorities: [Quality]\n"
        "composition:\n  traits: [trait-base]\n  rules: []\n  skills: []\n"
        "injection: Host injection.\n"
    )
    # guest role — composes the unique skill
    guest_dir = lib / "roles" / "guest"
    guest_dir.mkdir(parents=True)
    (guest_dir / "config.yaml").write_text(
        "name: guest\npriorities: [Quality]\n"
        "composition:\n  traits: [trait-base]\n  rules: []\n  skills: [guest-only-skill]\n"
        "injection: Guest injection.\n"
    )

    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(
        project / "ai-hats.yaml"
    )
    return project, lib


def test_claude_materialize_runtime_skills_returns_plugin_dir_arg(tmp_path):
    """ClaudeProvider returns --plugin-dir with a directory that holds the skills."""
    import shutil
    from ai_hats.composer import ResolvedComponent
    from ai_hats.models import ComponentType

    skill_src = tmp_path / "lib" / "guest-only-skill"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text(
        "---\nname: guest-only-skill\ndescription: x\n---\n"
    )

    result_skills = [
        ResolvedComponent(
            name="guest-only-skill",
            component_type=ComponentType.SKILL,
            source_path=skill_src,
            injection="",
        )
    ]
    from ai_hats.composer import CompositionResult
    from ai_hats.models import HooksConfig

    result = CompositionResult(
        name="guest",
        priorities=[],
        rules=[],
        skills=result_skills,
        hooks=HooksConfig(),
        injections=[],
    )

    provider = ClaudeProvider()
    args = provider.materialize_runtime_skills(tmp_path, result, "test-sid")
    try:
        assert args[0] == "--plugin-dir"
        plugin_dir = Path(args[1])
        assert plugin_dir.is_dir()
        assert (plugin_dir / "skills" / "guest-only-skill" / "SKILL.md").exists()
    finally:
        shutil.rmtree(args[1], ignore_errors=True)


def test_gemini_materialize_runtime_skills_is_noop(tmp_path):
    """Gemini has no plugin-dir analog (HATS-367 follow-up)."""
    from ai_hats.composer import CompositionResult
    from ai_hats.models import HooksConfig

    result = CompositionResult(
        name="anything",
        priorities=[],
        rules=[],
        skills=[],
        hooks=HooksConfig(),
        injections=[],
    )
    assert GeminiProvider().materialize_runtime_skills(tmp_path, result, "test-sid") == []


def test_subagent_runner_threads_plugin_dir_through_cmd(
    project_with_two_roles, monkeypatch
):
    """End-to-end: SubAgentRunner.run('guest') passes --plugin-dir to subprocess.run
    and the plugin-dir contains guest's role-specific skill."""
    from ai_hats import runtime as runtime_mod

    project, _lib = project_with_two_roles
    # Initialise project on host (active_role mirrors host's skills = empty).
    asm = Assembler(project)
    asm.init()
    asm.set_role("host", provider_name="claude")

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # Snapshot plugin-dir contents BEFORE cleanup runs (which happens
        # in SubAgentRunner's finally block after this call returns).
        if "--plugin-dir" in cmd:
            pd = Path(cmd[cmd.index("--plugin-dir") + 1])
            captured["plugin_dir"] = pd
            captured["plugin_skills"] = (
                sorted(p.name for p in (pd / "skills").iterdir())
                if (pd / "skills").exists()
                else []
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(runtime_mod.subprocess, "run", _fake_run)

    runner = runtime_mod.SubAgentRunner(project)
    runner.run(role_name="guest", task="hi", isolation_mode="discard")

    cmd = captured["cmd"]
    assert "--plugin-dir" in cmd, f"--plugin-dir missing from spawned cmd: {cmd}"
    assert captured["plugin_skills"] == ["guest-only-skill"]
    # Cleanup ran in finally — directory should be gone now.
    assert not captured["plugin_dir"].exists()
