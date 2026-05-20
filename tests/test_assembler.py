"""Tests for assembly engine."""

import pytest
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import hooks_dir, rules_dir, runs_dir, skills_dir, state_md_path, tasks_dir


@pytest.fixture
def project_with_library(tmp_path):
    """Create a project directory with a minimal library."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    # Rule
    rule_dir = lib / "rules" / "test_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Test Rule")
    (rule_dir / "metadata.yaml").write_text("name: test_rule\n")

    # Skill
    skill_dir = lib / "skills" / "test_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Test Skill")

    # Trait
    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text("name: trait-base\ninjection: Base.\n")

    # Role A
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: test-role
priorities:
  - Quality
composition:
  traits:
    - trait-base
  rules:
    - test_rule
  skills:
    - test_skill
injection: Role injection.
""")

    # Role B (for override tests)
    role_b_dir = lib / "roles" / "other-role"
    role_b_dir.mkdir(parents=True)
    (role_b_dir / "config.yaml").write_text("""
name: other-role
priorities:
  - Speed
composition:
  traits:
    - trait-base
  rules: []
  skills: []
injection: Other role injection.
""")

    # Create project config
    config = ProjectConfig(provider="gemini", library_paths=[str(lib)])
    config.save(project / "ai-hats.yaml")

    return project, lib


def test_init_creates_structure(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / "ai-hats.yaml")

    asm = Assembler(project)
    asm.init()

    assert (rules_dir(project)).is_dir()
    assert (skills_dir(project)).is_dir()
    assert (hooks_dir(project)).is_dir()
    assert (tasks_dir(project)).is_dir()
    assert (runs_dir(project)).is_dir()
    assert (project / "ai-hats.yaml").exists()
    assert (state_md_path(project)).exists()


def test_init_is_idempotent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / "ai-hats.yaml")

    asm = Assembler(project)
    asm.init()
    asm.init()  # Second call should not fail

    assert (rules_dir(project)).is_dir()


def test_set_role(project_with_library):
    """HATS-407: set_role is runtime-bootstrap — composes, writes active_role,
    ensures provider scaffold, installs hooks, regenerates user-rules
    aggregator. No more _copy_components materialization of rules/skills."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    result = asm.set_role("test-role")

    assert result.name == "test-role"
    assert len(result.errors) == 0
    # Gemini inline path: ./GEMINI.md exists with role injection.
    assert (project / "GEMINI.md").exists()
    prompt = (project / "GEMINI.md").read_text()
    assert "Role injection" in prompt
    # HATS-407: rules/skills are NOT copied into the canonical library tree.
    # Composition resolves them in-memory via the library layers.
    assert not (rules_dir(project) / "test_rule").exists()
    assert not (skills_dir(project) / "test_skill").exists()


def test_set_role_with_claude(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    asm.set_role("test-role", provider_name="claude")
    # HATS-294: ./CLAUDE.md is a thin scaffold importing only user-rules via
    # imports.md. Role injection is composed per-session by the provider, not
    # materialized to disk.
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in asm.composer.compose("test-role").role_injection


# HATS-407: rollback / _backup / _restore_backup removed — git is the
# user-facing recovery path. Tests below were retired with the helpers.


def test_clean(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    asm.clean()
    # Rules dir is wiped by clean() — empty (no files) or absent.
    rdir = rules_dir(project)
    assert not rdir.exists() or list(rdir.iterdir()) == []


def test_status(project_with_library):
    """HATS-407: status.health reflects on-disk artefacts only.
    With per-session compose, rules/skills are NOT materialized into the
    canonical tree, so the only verifiable disk pieces are imports.md and
    the provider system prompt."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    status = asm.status()
    assert status["role"] == "test-role"
    assert status["provider"] == "gemini"
    assert status["tree"] is not None
    assert "imports.md" in status["health"]
    assert status["health"]["imports.md"] == "OK"
    assert status["health"]["system_prompt"] == "OK"


def test_bump(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    result = asm.bump()
    assert result is not None
    assert result.name == "test-role"


def test_set_role_then_switch_provider(project_with_library):
    """Switching provider must regenerate system prompt for the new provider.

    Regression: setting role with gemini created GEMINI.md, then wrapping
    with claude found no CLAUDE.md — agent saw no instructions.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    # Set role for gemini (default)
    asm.set_role("test-role")
    assert (project / "GEMINI.md").exists()
    assert "Role injection" in (project / "GEMINI.md").read_text()

    # Now switch to claude — scaffold + .claude/CLAUDE.md aggregator carry
    # the role content (HATS-284/285).
    asm.set_role("test-role", provider_name="claude")
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in asm.composer.compose("test-role").role_injection

    # Profile must track the new provider
    from ai_hats.models import ProjectConfig

    profile = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert profile.provider == "claude"
    assert profile.active_role == "test-role"


def test_wrap_reassembles_on_provider_mismatch(project_with_library):
    """WrapRunner must auto-reassemble when provider differs from profile.

    Scenario: role set with gemini, then `ai-hats wrap claude` — should
    automatically rebuild CLAUDE.md before launching.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")  # provider=gemini

    # Simulate what WrapRunner.run() does on provider mismatch
    from ai_hats.models import ProjectConfig

    profile = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert profile.provider == "gemini"

    target_provider = "claude"
    if profile.active_role and profile.provider != target_provider:
        asm.set_role(profile.active_role, provider_name=target_provider)

    # CLAUDE.md is the scaffold; role content is in .agent/ai-hats/role.md
    # and surfaced via the .claude/CLAUDE.md aggregator (HATS-284/285).
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in asm.composer.compose("test-role").role_injection

    # Profile updated
    profile = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert profile.provider == "claude"


def test_wrap_uses_default_role_when_no_active_role(project_with_library):
    """When no role is set but default_role is configured, wrap should apply it."""
    project, lib = project_with_library
    # Set default_role in config
    from ai_hats.models import ProjectConfig

    config = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    config.default_role = "test-role"
    config.save(project / "ai-hats.yaml")

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    # Don't call set_role — simulate fresh project with only default_role

    from ai_hats.models import ProjectConfig

    profile = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert profile.active_role == ""  # No role set yet

    # Simulate what WrapRunner does: pick default_role
    effective_role = profile.active_role or config.default_role
    assert effective_role == "test-role"

    # Apply it (as WrapRunner would)
    asm.set_role(effective_role, provider_name="claude")
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in asm.composer.compose("test-role").role_injection


def test_preserve_local_rules(project_with_library):
    """Project-local rules should survive role reassembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    # Add a project-local rule
    local_rule = rules_dir(project) / "my_local_rule"
    local_rule.mkdir(parents=True)
    (local_rule / "rule.md").write_text("# My Local Rule")

    # Re-set role
    asm.set_role("test-role")

    # Local rule should still exist
    assert (rules_dir(project) / "my_local_rule" / "rule.md").exists()


# HATS-407: rollback / _backup / _restore_backup helpers removed.
# test_rollback_restores_previous_role, test_rollback_cleans_up_backup_dir,
# test_rollback_returns_false_when_no_backup retired with them.


def test_claude_build_session_prompt_creates_temp_file(project_with_library):
    """ClaudeProvider.build_session_prompt() creates temp file with override prompt."""
    from ai_hats.providers import ClaudeProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    # Set base role so CLAUDE.md exists with project content
    asm.set_role("test-role", provider_name="claude")
    # Add project-local content after markers
    claude_md = project / "CLAUDE.md"
    existing = claude_md.read_text()
    claude_md.write_text(existing + "\n# My Project Rules\nDo stuff.\n")

    # Build override for other-role
    provider = ClaudeProvider()
    result = asm.composer.compose("other-role")
    args, env = provider.build_session_prompt(project, result, "test-sid")

    # HATS-307: args now include --system-prompt-file AND --plugin-dir
    assert args[0] == "--system-prompt-file"
    assert "--plugin-dir" in args
    override_path = Path(args[1])
    plugin_dir = Path(args[args.index("--plugin-dir") + 1])
    assert override_path.exists()
    assert plugin_dir.is_dir()

    content = override_path.read_text()
    # Override prompt is injected
    assert "Other role injection" in content
    # Project-local content is preserved
    assert "My Project Rules" in content
    # Original role injection is NOT present
    assert "Role injection." not in content

    # Cleanup
    override_path.unlink()
    import shutil as _shutil
    _shutil.rmtree(plugin_dir, ignore_errors=True)


def test_claude_build_session_prompt_materializes_role_skills_in_plugin_dir(project_with_library):
    """HATS-307: spawned role's skills must end up under --plugin-dir/skills/."""
    import shutil as _shutil
    from ai_hats.providers import ClaudeProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    # Active role has NO skills (other-role); the spawned role (test-role)
    # composes test_skill — exactly the HATS-307 scenario.
    asm.set_role("other-role", provider_name="claude")

    provider = ClaudeProvider()
    result = asm.composer.compose("test-role")
    args, _ = provider.build_session_prompt(project, result, "test-sid")

    assert "--plugin-dir" in args
    plugin_dir = Path(args[args.index("--plugin-dir") + 1])
    try:
        assert (plugin_dir / ".claude-plugin" / "plugin.json").exists()
        assert (plugin_dir / "skills" / "test_skill" / "SKILL.md").exists()
    finally:
        Path(args[1]).unlink()
        _shutil.rmtree(plugin_dir, ignore_errors=True)


def test_claude_build_session_prompt_does_not_modify_project_claude_md(project_with_library):
    """build_session_prompt() must never modify the project CLAUDE.md."""
    from ai_hats.providers import ClaudeProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")

    original_content = (project / "CLAUDE.md").read_text()

    provider = ClaudeProvider()
    result = asm.composer.compose("other-role")
    args, _ = provider.build_session_prompt(project, result, "test-sid")

    # CLAUDE.md unchanged
    assert (project / "CLAUDE.md").read_text() == original_content
    # Cleanup
    Path(args[1]).unlink()
    if "--plugin-dir" in args:
        import shutil as _shutil
        _shutil.rmtree(args[args.index("--plugin-dir") + 1], ignore_errors=True)


def test_gemini_build_session_prompt_creates_rules_dir(project_with_library):
    """GeminiProvider.build_session_prompt() creates session rules dir with override."""
    import shutil
    from ai_hats.providers import GeminiProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")  # sets up .agent/rules/

    provider = GeminiProvider()
    result = asm.composer.compose("other-role")
    args, env = provider.build_session_prompt(project, result, "test-sid")

    assert args == []
    assert "GEMINI_CLI_PROJECT_RULES_PATH" in env
    rules_dir = Path(env["GEMINI_CLI_PROJECT_RULES_PATH"])
    assert rules_dir.exists()

    # Mandatory role file exists
    mandatory = rules_dir / "00_MANDATORY_ROLE.md"
    assert mandatory.exists()
    assert "Other role injection" in mandatory.read_text()

    # HATS-407: library-sourced rules are no longer materialized under
    # `<ai_hats_dir>/library/rules/`, so the per-session rules-dir copy
    # of test_rule is gone. Always-on rules + role injection still reach
    # the agent via 00_MANDATORY_ROLE.md (the inline composed prompt).
    assert not (rules_dir / "test_rule").exists()

    # GEMINI.md not touched
    assert "Role injection" in (project / "GEMINI.md").read_text()

    # Cleanup
    shutil.rmtree(rules_dir)


# HATS-407: _backup() removed — test_backup_survives_self_referential_symlinks_in_provider_skills retired.


# --------------------------------------------------------------------- #
# HATS-317 — one-shot .gitignore entry (replaces dynamic managed block)
# --------------------------------------------------------------------- #


def test_gitignore_init_writes_single_line(project_with_library):
    """`init` ensures `.agent/ai-hats/` is in .gitignore. No managed block."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    content = (project / ".gitignore").read_text()
    assert ".agent/ai-hats/" in content
    assert "AI-HATS:START" not in content
    assert "AI-HATS:END" not in content


def test_gitignore_init_idempotent(project_with_library):
    """Re-running `init` doesn't duplicate the .gitignore line."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    first = (project / ".gitignore").read_text()
    asm.init()
    second = (project / ".gitignore").read_text()
    assert first == second
    assert first.count(".agent/ai-hats/") == 1


def test_gitignore_init_preserves_user_content(project_with_library):
    """User lines remain; the ai-hats entry is appended without disturbing them."""
    project, lib = project_with_library
    (project / ".gitignore").write_text("# user header\n*.pyc\nbuild/\n")

    asm = Assembler(project, library_paths=[lib])
    asm.init()

    content = (project / ".gitignore").read_text()
    for fragment in ("# user header", "*.pyc", "build/", ".agent/ai-hats/"):
        assert fragment in content


def test_gitignore_set_role_does_not_touch_gitignore(project_with_library):
    """`set_role` is a no-op on .gitignore (HATS-317 removed the dynamic block)."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    before = (project / ".gitignore").read_text()
    asm.set_role("test-role", provider_name="claude")
    after = (project / ".gitignore").read_text()
    assert before == after


def test_gitignore_opt_out_skips_write(project_with_library):
    """`manage_gitignore=false` keeps init from writing any line."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.project_config.manage_gitignore = False
    asm.init()
    gi = project / ".gitignore"
    if gi.exists():
        assert ".agent/ai-hats/" not in gi.read_text()
    else:
        # No .gitignore created when opt-out is set on a clean project.
        assert True


# --------------------------------------------------------------------- #
# HATS-155 — manifest-driven .agent/{hooks,skills}/ management
# --------------------------------------------------------------------- #


# HATS-407: _copy_components removed — set_role no longer materializes
# rules/skills/hooks under the canonical library tree. Library overlay tests
# (managed-manifest, library-sourced skill survival) are retired; per-session
# compose resolves these in memory.


def test_managed_manifest_absent_after_set_role(project_with_library):
    """HATS-407: with _copy_components removed, set_role no longer writes
    .ai-hats-managed for skills/hooks/rules — composition resolves them in
    memory via library_paths and the canonical tree stays minimal."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert not (skills_dir(project) / ".ai-hats-managed").exists()
    assert not (hooks_dir(project) / ".ai-hats-managed").exists()


def test_user_hook_survives_bump(project_with_library):
    """User-authored file in .agent/hooks/ must not be wiped by re-assembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    # Ensure the dir exists (post-HATS-407 it may be empty until the user
    # drops a file in it — _copy_components no longer pre-populates).
    hooks_dir(project).mkdir(parents=True, exist_ok=True)
    user_hook = hooks_dir(project) / "my-custom.sh"
    user_hook.write_text("#!/usr/bin/env bash\necho custom\n")

    asm.bump()

    assert user_hook.exists(), "User hook must survive bump"
    assert user_hook.read_text() == "#!/usr/bin/env bash\necho custom\n"


def test_user_skill_dir_survives_bump(project_with_library):
    """User-authored subdir in .agent/skills/ must not be wiped by re-assembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    skills_dir(project).mkdir(parents=True, exist_ok=True)
    user_skill = skills_dir(project) / "my_local_skill"
    user_skill.mkdir()
    (user_skill / "SKILL.md").write_text("# local\n")

    asm.bump()

    assert (user_skill / "SKILL.md").exists()
    # HATS-407: library-sourced skills are no longer copied into the
    # canonical tree. They are resolved in-memory at session-compose time.
    assert not (skills_dir(project) / "test_skill").exists()


# --------------------------------------------------------------------- #
# HATS-251 — dev_rule_tool_call_hygiene wired as always-on
# --------------------------------------------------------------------- #


def test_tool_call_hygiene_is_always_on():
    """dev_rule_tool_call_hygiene must appear in system prompt (HATS-251)."""
    from pathlib import Path

    from ai_hats.composer import CompositionResult, ResolvedComponent
    from ai_hats.models import ComponentType, HooksConfig
    from ai_hats.providers import ALWAYS_ON_RULES, ClaudeProvider

    assert "dev_rule_tool_call_hygiene" in ALWAYS_ON_RULES

    rule = ResolvedComponent(
        name="dev_rule_tool_call_hygiene",
        component_type=ComponentType.RULE,
        source_path=Path("/dev/null"),
        injection="# Rule: Tool-Call Hygiene\nUse dedicated tools over Bash.",
    )
    result = CompositionResult(
        name="test",
        priorities=[],
        rules=[rule],
        skills=[],
        hooks=HooksConfig(),
        injections=[],
    )
    prompt = ClaudeProvider().build_system_prompt(result)
    assert "dev_rule_tool_call_hygiene" in prompt
    assert "Tool-Call Hygiene" in prompt


# --------------------------------------------------------------------- #
# HATS-380 — `<ai_hats_dir>` placeholder must be expanded before reaching
# the agent (skill bodies, rule bodies, role/trait injection).
# --------------------------------------------------------------------- #


@pytest.fixture
def project_with_placeholder_library(tmp_path):
    """Library where every injectable surface embeds `<ai_hats_dir>`."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    rule_dir = lib / "rules" / "ph_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("Rule body refs <ai_hats_dir>/state.\n")
    (rule_dir / "metadata.yaml").write_text("name: ph_rule\n")

    skill_dir = lib / "skills" / "ph_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: ph_skill\ndescription: ph\n---\n"
        "Write reports to <ai_hats_dir>/sessions/retros/.\n"
    )

    trait_dir = lib / "traits" / "trait-ph"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-ph\ninjection: 'Trait sees <ai_hats_dir>/tracker.'\n"
    )

    role_dir = lib / "roles" / "ph-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: ph-role\n"
        "priorities: [Quality]\n"
        "composition:\n"
        "  traits: [trait-ph]\n"
        "  rules: [ph_rule]\n"
        "  skills: [ph_skill]\n"
        "injection: 'Role writes to <ai_hats_dir>/sessions/audits/.'\n"
    )

    config = ProjectConfig(provider="claude", library_paths=[str(lib)])
    config.save(project / "ai-hats.yaml")
    return project, lib


def _assert_no_literal_placeholder(*paths: Path) -> None:
    offenders = [p for p in paths if p.is_file() and "<ai_hats_dir>" in p.read_text()]
    assert not offenders, f"placeholder leaked into: {offenders}"


def test_canonical_dir_has_no_literal_placeholder(project_with_placeholder_library):
    """HATS-294: only ``imports.md`` is materialized on disk; it imports
    user-rules only and must be placeholder-free. Framework content with
    placeholders is composed per-session — verified separately via the
    Provider.build_session_prompt path.
    """
    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="claude")

    canonical = project / ".agent" / "ai-hats"
    _assert_no_literal_placeholder(canonical / "imports.md")


def test_gemini_inline_prompt_has_no_literal_placeholder(
    project_with_placeholder_library,
):
    """`./GEMINI.md` injection block (Gemini's set_role path) must be expanded."""
    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="gemini")

    gemini_md = project / "GEMINI.md"
    assert gemini_md.exists()
    content = gemini_md.read_text()
    assert "<ai_hats_dir>" not in content
    # Spot-check substitution landed in the injected block.
    assert ".agent/ai-hats/sessions/audits/" in content


def test_gemini_build_session_prompt_has_no_literal_placeholder(
    project_with_placeholder_library,
):
    """Gemini override prompt (`00_MANDATORY_ROLE.md`) must be expanded."""
    from ai_hats.composer import Composer
    from ai_hats.providers import GeminiProvider
    from ai_hats.resolver import LibraryResolver

    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = Composer(LibraryResolver([lib])).compose("ph-role")

    _, env = GeminiProvider().build_session_prompt(project, result, "test-sid")
    override = Path(env["GEMINI_CLI_PROJECT_RULES_PATH"]) / "00_MANDATORY_ROLE.md"
    content = override.read_text()
    assert "<ai_hats_dir>" not in content
    assert ".agent/ai-hats/sessions/audits/" in content


def test_claude_build_session_prompt_has_no_literal_placeholder(
    project_with_placeholder_library,
):
    """Claude --system-prompt-file content must be expanded."""
    from ai_hats.composer import Composer
    from ai_hats.providers import ClaudeProvider
    from ai_hats.resolver import LibraryResolver

    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="claude")
    result = Composer(LibraryResolver([lib])).compose("ph-role")

    args, _ = ClaudeProvider().build_session_prompt(project, result, "test-sid")
    # build_session_prompt returns ["--system-prompt-file", <path>]
    prompt_file = Path(args[args.index("--system-prompt-file") + 1])
    content = prompt_file.read_text()
    assert "<ai_hats_dir>" not in content
    assert ".agent/ai-hats/sessions/audits/" in content


def test_subagent_meta_prompt_has_no_literal_placeholder(
    project_with_placeholder_library,
):
    """HATS-380 residual gap: SubAgentRunner._build_meta_prompt must expand
    `<ai_hats_dir>` in result.merged_injection. Roles like session-reviewer
    (auto-spawned by reflect-session) carry the literal in their injection."""
    from ai_hats.providers import get_provider
    from ai_hats.runtime import SubAgentRunner

    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="claude")
    result = asm.composer.compose("ph-role")

    runner = SubAgentRunner(project)
    meta_prompt = runner._build_meta_prompt(
        result=result, provider=get_provider("claude"), task="", ticket_id="",
    )

    assert "<ai_hats_dir>" not in meta_prompt
    # Spot-check both trait + role injection landed expanded.
    assert ".agent/ai-hats/tracker" in meta_prompt
    assert ".agent/ai-hats/sessions/audits/" in meta_prompt
