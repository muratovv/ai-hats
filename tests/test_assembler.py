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

    # Role
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
