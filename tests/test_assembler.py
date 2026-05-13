"""Tests for assembly engine."""

import pytest
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import runs_dir


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

    assert (project / ".agent" / "rules").is_dir()
    assert (project / ".agent" / "skills").is_dir()
    assert (project / ".agent" / "hooks").is_dir()
    assert (project / ".agent" / "backlog" / "tasks").is_dir()
    assert (runs_dir(project)).is_dir()
    assert (project / "ai-hats.yaml").exists()
    assert (project / ".agent" / "STATE.md").exists()


def test_init_is_idempotent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / "ai-hats.yaml")

    asm = Assembler(project)
    asm.init()
    asm.init()  # Second call should not fail

    assert (project / ".agent" / "rules").is_dir()


def test_set_role(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    result = asm.set_role("test-role")

    assert result.name == "test-role"
    assert len(result.errors) == 0
    assert (project / ".agent" / "rules" / "test_rule" / "rule.md").exists()
    assert (project / ".agent" / "skills" / "test_skill" / "SKILL.md").exists()
    assert (project / "GEMINI.md").exists()

    prompt = (project / "GEMINI.md").read_text()
    assert "Role injection" in prompt
    # Rules are copied to .agent/rules/ but only always-on rules appear in prompt
    # Context-specific rules load on demand via native provider skills
    assert (project / ".agent" / "rules" / "test_rule" / "rule.md").exists()


def test_set_role_with_claude(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    asm.set_role("test-role", provider_name="claude")
    # HATS-284/285: ./CLAUDE.md is now a thin scaffold; role content lives
    # in .claude/CLAUDE.md aggregator + .agent/ai-hats/role.md.
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in (project / ".agent" / "ai-hats" / "role.md").read_text()


def test_rollback(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    # Verify role is set
    assert (project / ".agent" / "rules" / "test_rule").exists()

    # Now rollback
    assert asm.rollback()

    # Rules from role should be gone (restored to pre-set state)
    # Note: the backup was taken before set_role cleaned, so it restores the pre-set state


def test_clean(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    asm.clean()
    # Rules dir should be empty (no files, just directory)
    rules_contents = list((project / ".agent" / "rules").iterdir())
    assert len(rules_contents) == 0


def test_status(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    status = asm.status()
    assert status["role"] == "test-role"
    assert status["provider"] == "gemini"
    assert status["tree"] is not None
    assert "rule:test_rule" in status["health"]
    assert status["health"]["rule:test_rule"] == "OK"


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
    assert "Role injection" in (project / ".agent" / "ai-hats" / "role.md").read_text()

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
    assert "Role injection" in (project / ".agent" / "ai-hats" / "role.md").read_text()

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
    assert "Role injection" in (project / ".agent" / "ai-hats" / "role.md").read_text()


def test_preserve_local_rules(project_with_library):
    """Project-local rules should survive role reassembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    # Add a project-local rule
    local_rule = project / ".agent" / "rules" / "my_local_rule"
    local_rule.mkdir(parents=True)
    (local_rule / "rule.md").write_text("# My Local Rule")

    # Re-set role
    asm.set_role("test-role")

    # Local rule should still exist
    assert (project / ".agent" / "rules" / "my_local_rule" / "rule.md").exists()


def test_rollback_restores_previous_role(project_with_library):
    """After set_role(B), rollback() restores role A's prompt and profile."""
    from ai_hats.models import ProjectConfig

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    # Set role A — content lives in canonical role.md (HATS-282).
    asm.set_role("test-role", provider_name="claude")
    role_md = project / ".agent" / "ai-hats" / "role.md"
    prompt_a = role_md.read_text()
    assert "Role injection" in prompt_a
    profile_a = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert profile_a.active_role == "test-role"

    # Set role B (override)
    asm.set_role("other-role", provider_name="claude")
    prompt_b = role_md.read_text()
    assert "Other role injection" in prompt_b
    profile_b = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert profile_b.active_role == "other-role"

    # Rollback → should restore role A
    assert asm.rollback()
    prompt_restored = role_md.read_text()
    assert "Role injection" in prompt_restored
    assert "Other role injection" not in prompt_restored
    profile_restored = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert profile_restored.active_role == "test-role"


def test_rollback_cleans_up_backup_dir(project_with_library):
    """rollback() should remove the temp backup dir and .last_backup ref."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    asm.set_role("test-role")

    # .last_backup ref should exist after set_role
    ref_path = project / ".agent" / ".last_backup"
    assert ref_path.exists()
    backup_dir = Path(ref_path.read_text().strip())
    assert backup_dir.exists()

    # Rollback
    asm.rollback()

    # Backup dir and ref should be cleaned up
    assert not backup_dir.exists()
    assert not ref_path.exists()


def test_rollback_returns_false_when_no_backup(project_with_library):
    """rollback() returns False when there's nothing to rollback to."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    assert not asm.rollback()


def test_claude_build_override_creates_temp_file(project_with_library):
    """ClaudeProvider.build_override() creates temp file with override prompt."""
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
    args, env = provider.build_override(project, result, None)

    assert len(args) == 2
    assert args[0] == "--system-prompt-file"
    override_path = Path(args[1])
    assert override_path.exists()

    content = override_path.read_text()
    # Override prompt is injected
    assert "Other role injection" in content
    # Project-local content is preserved
    assert "My Project Rules" in content
    # Original role injection is NOT present
    assert "Role injection." not in content

    # Cleanup
    override_path.unlink()


def test_claude_build_override_does_not_modify_project_claude_md(project_with_library):
    """build_override() must never modify the project CLAUDE.md."""
    from ai_hats.providers import ClaudeProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")

    original_content = (project / "CLAUDE.md").read_text()

    provider = ClaudeProvider()
    result = asm.composer.compose("other-role")
    args, _ = provider.build_override(project, result, None)

    # CLAUDE.md unchanged
    assert (project / "CLAUDE.md").read_text() == original_content
    # Cleanup
    Path(args[1]).unlink()


def test_gemini_build_override_creates_rules_dir(project_with_library):
    """GeminiProvider.build_override() creates session rules dir with override."""
    import shutil
    from ai_hats.providers import GeminiProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")  # sets up .agent/rules/

    provider = GeminiProvider()
    result = asm.composer.compose("other-role")
    args, env = provider.build_override(project, result, None)

    assert args == []
    assert "GEMINI_CLI_PROJECT_RULES_PATH" in env
    rules_dir = Path(env["GEMINI_CLI_PROJECT_RULES_PATH"])
    assert rules_dir.exists()

    # Mandatory role file exists
    mandatory = rules_dir / "00_MANDATORY_ROLE.md"
    assert mandatory.exists()
    assert "Other role injection" in mandatory.read_text()

    # Project rules copied
    assert (rules_dir / "test_rule").exists()

    # GEMINI.md not touched
    assert "Role injection" in (project / "GEMINI.md").read_text()

    # Cleanup
    shutil.rmtree(rules_dir)


def test_backup_survives_self_referential_symlinks_in_provider_skills(
    project_with_library,
):
    """Regression: a self-referential symlink under .gemini/skills or
    .claude/skills must not cause _backup() to loop until ELOOP.

    Repro: user had `.gemini/skills/foo/foo -> .gemini/skills/foo` in a
    pre-existing project. shutil.copytree(..., symlinks=False) followed the
    link and recursed until the OS raised errno 62 ("Too many levels of
    symbolic links"), aborting `ai-hats self init` mid-flight.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    # Plant a self-referential symlink under .gemini/skills, mirroring the
    # real-world shape from the bug report.
    gemini_skills = project / ".gemini" / "skills" / "subagent-analyzer"
    gemini_skills.mkdir(parents=True)
    (gemini_skills / "SKILL.md").write_text("# stub\n")
    (gemini_skills / "subagent-analyzer").symlink_to(gemini_skills)

    # set_role invokes _backup(); must not raise shutil.Error/OSError.
    asm.set_role("test-role")

    # After set_role, a backup exists. Verify the self-symlink survived as a
    # link (not dereferenced, not traversed).
    ref_path = project / ".agent" / ".last_backup"
    backup_dir = Path(ref_path.read_text().strip())
    backup_symlink = backup_dir / ".gemini" / "skills" / "subagent-analyzer" / "subagent-analyzer"
    assert backup_symlink.is_symlink()


# --------------------------------------------------------------------- #
# HATS-141 — managed .gitignore block
# --------------------------------------------------------------------- #


def _read_block(project: Path) -> str:
    """Return the content between AI-HATS markers in .gitignore (empty if absent)."""
    from ai_hats.assembler import GITIGNORE_END, GITIGNORE_START

    gi = (project / ".gitignore").read_text()
    if GITIGNORE_START not in gi or GITIGNORE_END not in gi:
        return ""
    start = gi.index(GITIGNORE_START)
    end = gi.index(GITIGNORE_END) + len(GITIGNORE_END)
    return gi[start:end]


def test_gitignore_block_created_on_fresh_repo(project_with_library):
    """set_role writes .gitignore with managed block when file is absent."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")

    block = _read_block(project)
    # Static entries always present
    assert ".agent/.last_backup" in block
    # Composed rule + skill tracked per-name (no blanket directory ignores)
    assert ".agent/rules/test_rule/" in block
    assert ".agent/rules/.library_rules" in block
    assert ".agent/skills/test_skill/" in block
    assert ".agent/skills/.ai-hats-managed" in block
    assert ".claude/skills/test_skill/" in block
    assert ".claude/skills/.ai-hats-managed" in block
    # No blanket directory-level ignores for .agent/{hooks,mcp,skills}/
    assert "\n.agent/hooks/\n" not in block
    assert "\n.agent/mcp/\n" not in block
    assert "\n.agent/skills/\n" not in block
    # test-role declares no hooks/mcp → no manifest entries for those dirs
    assert ".agent/hooks/.ai-hats-managed" not in block
    assert ".agent/mcp/.ai-hats-managed" not in block
    # Gemini side not installed for this role → not present
    assert ".gemini/skills/" not in block


def test_gitignore_preserves_user_content(project_with_library):
    """User-authored .gitignore lines outside markers survive across runs."""
    project, lib = project_with_library
    (project / ".gitignore").write_text("# user header\n*.pyc\nbuild/\n")

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    content = (project / ".gitignore").read_text()
    assert "# user header" in content
    assert "*.pyc" in content
    assert "build/" in content
    assert ".agent/skills/test_skill/" in content

    # Re-run must keep user content and produce byte-identical file
    before = content
    asm.set_role("test-role")
    after = (project / ".gitignore").read_text()
    assert before == after


def test_gitignore_block_self_heals_on_role_switch(project_with_library):
    """Switching to a role without test_skill drops stale entries from the block."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")
    assert ".claude/skills/test_skill/" in _read_block(project)

    asm.set_role("other-role", provider_name="claude")
    block = _read_block(project)
    assert ".claude/skills/test_skill/" not in block
    assert ".agent/rules/test_rule/" not in block
    assert ".agent/skills/test_skill/" not in block
    # Manifest entries drop entirely when the target dir has no managed files.
    assert ".agent/skills/.ai-hats-managed" not in block
    # Baseline static entry stays.
    assert ".agent/.last_backup" in block


def test_gitignore_does_not_list_user_local_rule(project_with_library):
    """Project-local rules (not in .library_rules) stay out of the block."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    # Plant a user-local rule before first set_role — _clean preserves it.
    local_rule = project / ".agent" / "rules" / "my_local"
    local_rule.mkdir(parents=True)
    (local_rule / "rule.md").write_text("# mine\n")

    asm.set_role("test-role")
    block = _read_block(project)
    assert ".agent/rules/my_local/" not in block
    # Sanity: library rule still listed.
    assert ".agent/rules/test_rule/" in block


def test_gitignore_opt_out_removes_block(project_with_library):
    """manage_gitignore=false strips the block on next run; user lines untouched."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    assert ".agent/skills/test_skill/" in _read_block(project)

    # Toggle flag and persist, then re-apply role.
    asm.project_config.manage_gitignore = False
    asm.project_config.save(asm.config_path)
    (project / ".gitignore").write_text((project / ".gitignore").read_text() + "\n# user tail\n")

    asm.set_role("test-role")
    content = (project / ".gitignore").read_text()
    from ai_hats.assembler import GITIGNORE_END, GITIGNORE_START

    assert GITIGNORE_START not in content
    assert GITIGNORE_END not in content
    assert "# user tail" in content


# --------------------------------------------------------------------- #
# HATS-155 — manifest-driven .agent/{hooks,mcp,skills}/ management
# --------------------------------------------------------------------- #


def test_managed_manifest_written_for_skills(project_with_library):
    """set_role drops a .ai-hats-managed manifest listing managed skills."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    manifest = project / ".agent" / "skills" / ".ai-hats-managed"
    assert manifest.exists(), "Expected .ai-hats-managed manifest in .agent/skills"
    assert manifest.read_text().splitlines() == ["test_skill"]


def test_managed_manifest_absent_when_no_entries(project_with_library):
    """Composition without hooks/mcp leaves those dirs without a manifest."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert not (project / ".agent" / "hooks" / ".ai-hats-managed").exists()
    assert not (project / ".agent" / "mcp" / ".ai-hats-managed").exists()


def test_user_hook_survives_bump(project_with_library):
    """User-authored file in .agent/hooks/ must not be wiped by re-assembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    user_hook = project / ".agent" / "hooks" / "my-custom.sh"
    user_hook.write_text("#!/usr/bin/env bash\necho custom\n")

    asm.bump()

    assert user_hook.exists(), "User hook must survive re-assembly"
    assert user_hook.read_text() == "#!/usr/bin/env bash\necho custom\n"


def test_user_skill_dir_survives_bump(project_with_library):
    """User-authored subdir in .agent/skills/ must not be wiped by re-assembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    user_skill = project / ".agent" / "skills" / "my_local_skill"
    user_skill.mkdir()
    (user_skill / "SKILL.md").write_text("# local\n")

    asm.bump()

    assert (user_skill / "SKILL.md").exists()
    # Library-sourced skill re-installed alongside.
    assert (project / ".agent" / "skills" / "test_skill" / "SKILL.md").exists()


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
        mcp=[],
        injections=[],
    )
    prompt = ClaudeProvider().build_system_prompt(result)
    assert "dev_rule_tool_call_hygiene" in prompt
    assert "Tool-Call Hygiene" in prompt
