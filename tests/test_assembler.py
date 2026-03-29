"""Tests for assembly engine."""

import pytest
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig


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
    assert (project / ".gitlog").is_dir()
    assert (project / "profile.json").exists()
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
    assert "Test Rule" in prompt


def test_set_role_with_claude(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    result = asm.set_role("test-role", provider_name="claude")
    assert (project / "CLAUDE.md").exists()
    assert "Role injection" in (project / "CLAUDE.md").read_text()


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


def test_whoami(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    info = asm.whoami()
    assert info["role"] == "test-role"
    assert info["provider"] == "gemini"


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

    # Now switch to claude — must create CLAUDE.md with the same content
    asm.set_role("test-role", provider_name="claude")
    assert (project / "CLAUDE.md").exists()
    assert "Role injection" in (project / "CLAUDE.md").read_text()

    # Profile must track the new provider
    from ai_hats.models import ProfileConfig
    profile = ProfileConfig.load(project / "profile.json")
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
    from ai_hats.models import ProfileConfig
    profile = ProfileConfig.load(project / "profile.json")
    assert profile.provider == "gemini"

    target_provider = "claude"
    if profile.active_role and profile.provider != target_provider:
        asm.set_role(profile.active_role, provider_name=target_provider)

    # CLAUDE.md must now exist with correct content
    assert (project / "CLAUDE.md").exists()
    prompt = (project / "CLAUDE.md").read_text()
    assert "Role injection" in prompt
    assert "Test Rule" in prompt

    # Profile updated
    profile = ProfileConfig.load(project / "profile.json")
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

    from ai_hats.models import ProfileConfig
    profile = ProfileConfig.load(project / "profile.json")
    assert profile.active_role == ""  # No role set yet

    # Simulate what WrapRunner does: pick default_role
    effective_role = profile.active_role or config.default_role
    assert effective_role == "test-role"

    # Apply it (as WrapRunner would)
    asm.set_role(effective_role, provider_name="claude")
    assert (project / "CLAUDE.md").exists()
    assert "Role injection" in (project / "CLAUDE.md").read_text()


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
    from ai_hats.models import ProfileConfig

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    # Set role A
    asm.set_role("test-role", provider_name="claude")
    prompt_a = (project / "CLAUDE.md").read_text()
    assert "Role injection" in prompt_a
    profile_a = ProfileConfig.load(project / "profile.json")
    assert profile_a.active_role == "test-role"

    # Set role B (override)
    asm.set_role("other-role", provider_name="claude")
    prompt_b = (project / "CLAUDE.md").read_text()
    assert "Other role injection" in prompt_b
    profile_b = ProfileConfig.load(project / "profile.json")
    assert profile_b.active_role == "other-role"

    # Rollback → should restore role A
    assert asm.rollback()
    prompt_restored = (project / "CLAUDE.md").read_text()
    assert "Role injection" in prompt_restored
    assert "Other role injection" not in prompt_restored
    profile_restored = ProfileConfig.load(project / "profile.json")
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
