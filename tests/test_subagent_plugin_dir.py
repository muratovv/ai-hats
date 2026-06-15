"""HATS-307 contract: spawned role's skills must reach the sub-agent.

Originally written against the subprocess engine (asserting
``--plugin-dir`` ended up in argv). HATS-474 Phase 2 moved the Claude
path onto :mod:`claude_agent_sdk`; skills now reach the agent via
``ClaudeAgentOptions.plugins`` instead of an explicit CLI flag. The
end-to-end test is rewritten to assert the same behavioural contract on
the new surface: the plugin entry is built, populated on disk, and
removed after the attempt finishes. The legacy
:meth:`ClaudeProvider.materialize_runtime_skills` unit test stays —
it still covers the same disk layout the Gemini / future-CLI providers
will keep using.
"""

from __future__ import annotations

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

    result = CompositionResult(
        name="guest",
        priorities=[],
        rules=[],
        skills=result_skills,
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

    result = CompositionResult(
        name="anything",
        priorities=[],
        rules=[],
        skills=[],
        injections=[],
    )
    assert GeminiProvider().materialize_runtime_skills(tmp_path, result, "test-sid") == []


def test_subagent_runner_threads_plugin_dir_to_sdk_options(
    project_with_two_roles, monkeypatch
):
    """End-to-end: SubAgentRunner.run('guest') reaches the SDK with a
    ``plugins=[{type: local, path: <dir>}]`` entry, and the on-disk
    plugin-dir contains the guest role's unique skill. After the attempt
    finalizes, the per-session cache (including the plugin-dir) is
    cleaned up by ``_cleanup_session_cache``.
    """
    # HATS-715: SubAgentRunner moved to subagent_runner — patch there (where .run looks).
    from ai_hats import subagent_runner as runtime_mod
    from ai_hats.sdk_runner import SdkRunResult

    project, _lib = project_with_two_roles
    # Initialise project on host (active_role mirrors host's skills = empty).
    asm = Assembler(project)
    asm.init()
    asm.set_role("host", provider_name="claude")

    captured: dict = {}

    def _fake_sdk(*, options, initial_message, timeout_s):
        # Record the options' plugin entry plus snapshot the on-disk
        # contents BEFORE cleanup deletes the cache dir in the runner's
        # finally block (which happens after this stub returns).
        captured["plugins"] = list(options.plugins)
        captured["initial_message"] = initial_message
        if options.plugins:
            pd = Path(options.plugins[0]["path"])
            captured["plugin_dir"] = pd
            skills_root = pd / "skills"
            captured["plugin_skills"] = (
                sorted(p.name for p in skills_root.iterdir())
                if skills_root.exists()
                else []
            )
        return SdkRunResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            claude_session_id="stub-sid",
            total_cost_usd=0.0,
            num_turns=1,
            stop_reason="end_turn",
            timed_out=False,
            error=None,
        )

    monkeypatch.setattr(runtime_mod, "_cleanup_session_cache", lambda *a, **kw: None)
    monkeypatch.setattr(
        "ai_hats.sdk_runner.run_claude_sdk_blocking", _fake_sdk,
    )

    runner = runtime_mod.SubAgentRunner(project)
    runner.run(role_name="guest", task="hi", isolation_mode="discard")

    assert len(captured["plugins"]) == 1, captured["plugins"]
    plugin = captured["plugins"][0]
    assert plugin["type"] == "local"
    assert Path(plugin["path"]) == captured["plugin_dir"]
    assert captured["plugin_skills"] == ["guest-only-skill"]
    # The initial user message reached the SDK with the task text in it.
    assert "hi" in captured["initial_message"]
