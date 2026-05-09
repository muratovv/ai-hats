"""Tests for the canonical layered writer (HATS-282)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_hats.assembler import (
    AGENT_DIR,
    CANONICAL_DIR,
    CANONICAL_MANIFEST,
    USER_RULES_SUBDIR,
    Assembler,
)


@pytest.fixture
def project_with_library(tmp_path: Path) -> Path:
    """Build a minimal project + library so Assembler can compose a real role."""
    project = tmp_path / "proj"
    project.mkdir()

    lib = project / "libraries"

    # Rule with body
    rule_a = lib / "rules" / "rule_a"
    rule_a.mkdir(parents=True)
    (rule_a / "rule.md").write_text("# rule_a\nDo A.\n")
    (rule_a / "metadata.yaml").write_text("name: rule_a\n")

    rule_b = lib / "rules" / "rule_b"
    rule_b.mkdir(parents=True)
    (rule_b / "rule.md").write_text("# rule_b\nDo B.\n")
    (rule_b / "metadata.yaml").write_text("name: rule_b\n")

    # Skill with frontmatter description
    skill_x = lib / "skills" / "skill_x"
    skill_x.mkdir(parents=True)
    (skill_x / "SKILL.md").write_text(
        "---\nname: skill_x\ndescription: Does X efficiently\n---\n# Skill X\n"
    )

    # Trait with rules + injection
    trait_alpha = lib / "traits" / "trait-alpha"
    trait_alpha.mkdir(parents=True)
    (trait_alpha / "config.yaml").write_text(
        """
name: trait-alpha
composition:
  rules:
    - rule_a
injection: |
  Alpha trait injection.
"""
    )

    trait_beta = lib / "traits" / "trait-beta"
    trait_beta.mkdir(parents=True)
    (trait_beta / "config.yaml").write_text(
        """
name: trait-beta
injection: |
  Beta trait injection.
"""
    )

    # Trait with empty injection
    trait_silent = lib / "traits" / "trait-silent"
    trait_silent.mkdir(parents=True)
    (trait_silent / "config.yaml").write_text(
        """
name: trait-silent
composition:
  rules:
    - rule_b
injection: ""
"""
    )

    # Role
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        """
name: test-role
priorities:
  - Reliability
  - Velocity
composition:
  traits:
    - trait-alpha
    - trait-beta
  skills:
    - skill_x
injection: |
  Role injection text.
"""
    )

    # ai-hats.yaml so Assembler.__init__ doesn't choke
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    return project


def _composer_result(project: Path, role: str = "test-role"):
    asm = Assembler(project)
    return asm, asm.composer.compose(role)


def test_write_canonical_creates_layout(project_with_library: Path) -> None:
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    assert (canonical / "priorities.md").exists()
    assert (canonical / "role.md").exists()
    assert (canonical / "skills_index.md").exists()
    assert (canonical / "traits" / "trait-alpha.md").exists()
    assert (canonical / "traits" / "trait-beta.md").exists()
    # trait-silent had empty injection — and isn't in trait_injections in T1 — so no file
    assert not (canonical / "traits" / "trait-silent.md").exists()
    assert (canonical / "rules" / "rule_a.md").exists()
    assert (canonical / USER_RULES_SUBDIR).is_dir()
    assert (canonical / CANONICAL_MANIFEST).exists()


def test_write_canonical_manifest_contents(project_with_library: Path) -> None:
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    manifest = asm._read_canonical_manifest(canonical / CANONICAL_MANIFEST)
    assert "priorities.md" in manifest
    assert "role.md" in manifest
    assert "skills_index.md" in manifest
    assert "traits/trait-alpha.md" in manifest
    assert "traits/trait-beta.md" in manifest
    assert "rules/rule_a.md" in manifest
    # user-rules/ never tracked
    assert not any(p.startswith(f"{USER_RULES_SUBDIR}/") for p in manifest)


def test_write_canonical_content_correctness(project_with_library: Path) -> None:
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    assert (canonical / "traits" / "trait-alpha.md").read_text().strip() == result.trait_injections[
        "trait-alpha"
    ]
    assert (canonical / "rules" / "rule_a.md").read_text().strip() == result.rules[
        0
    ].injection.strip()
    priorities = (canonical / "priorities.md").read_text()
    assert "1. Reliability" in priorities
    assert "2. Velocity" in priorities
    role_text = (canonical / "role.md").read_text()
    assert "Role injection text." in role_text


def test_write_canonical_skills_index_with_descriptions(
    project_with_library: Path,
) -> None:
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    body = (canonical / "skills_index.md").read_text()
    assert "**skill_x**" in body
    assert "Does X efficiently" in body


def test_write_canonical_idempotent_no_op(project_with_library: Path) -> None:
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    target = canonical / "rules" / "rule_a.md"
    first_mtime = target.stat().st_mtime_ns

    # Sleep tiny bit to make sure mtime would change if we wrote again.
    time.sleep(0.01)
    asm.write_canonical(result)

    assert target.stat().st_mtime_ns == first_mtime


def test_write_canonical_stale_cleanup(project_with_library: Path) -> None:
    """Trait removed from composition disappears from canonical on next write."""
    asm, result_first = _composer_result(project_with_library)
    asm.write_canonical(result_first)

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    assert (canonical / "traits" / "trait-beta.md").exists()

    # Mutate role to drop trait-beta, recompose, rewrite.
    role_yaml = project_with_library / "libraries" / "roles" / "test-role" / "config.yaml"
    role_yaml.write_text(
        """
name: test-role
priorities:
  - Reliability
composition:
  traits:
    - trait-alpha
  skills:
    - skill_x
injection: |
  Role injection text.
"""
    )
    asm2 = Assembler(project_with_library)
    result_second = asm2.composer.compose("test-role")
    asm2.write_canonical(result_second)

    assert (canonical / "traits" / "trait-alpha.md").exists()
    assert not (canonical / "traits" / "trait-beta.md").exists()


def test_write_canonical_user_rules_protected(project_with_library: Path) -> None:
    """Files under user-rules/ survive cleanup regardless of MANAGED."""
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    user_file = canonical / USER_RULES_SUBDIR / "my-conv.md"
    user_file.write_text("# project-specific rule\n")

    # Rewrite with same composition — user file must survive.
    asm.write_canonical(result)
    assert user_file.exists()

    # Rewrite with different composition (drop trait-beta) — still survives.
    role_yaml = project_with_library / "libraries" / "roles" / "test-role" / "config.yaml"
    role_yaml.write_text(
        """
name: test-role
priorities:
  - Reliability
composition:
  traits:
    - trait-alpha
injection: |
  Role injection text.
"""
    )
    asm2 = Assembler(project_with_library)
    asm2.write_canonical(asm2.composer.compose("test-role"))
    assert user_file.exists()
    assert user_file.read_text() == "# project-specific rule\n"


def test_write_canonical_overlay_appended_to_role(project_with_library: Path) -> None:
    asm = Assembler(project_with_library)
    from ai_hats.models import OverlayConfig

    result = asm.composer.compose(
        "test-role", overlay=OverlayConfig(injection_append="Custom overlay text.")
    )
    asm.write_canonical(result)

    role_text = (project_with_library / AGENT_DIR / CANONICAL_DIR / "role.md").read_text()
    assert "Role injection text." in role_text
    assert "Custom overlay text." in role_text
    assert role_text.index("Role injection text.") < role_text.index("Custom overlay")


def test_write_canonical_empty_priorities_skipped(tmp_path: Path) -> None:
    """Role without priorities → no priorities.md and not in manifest."""
    project = tmp_path / "proj"
    project.mkdir()
    lib = project / "libraries"
    role_dir = lib / "roles" / "thin"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("name: thin\ninjection: |\n  Just role text.\n")
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    result = asm.composer.compose("thin")
    asm.write_canonical(result)

    canonical = project / AGENT_DIR / CANONICAL_DIR
    assert not (canonical / "priorities.md").exists()
    manifest = asm._read_canonical_manifest(canonical / CANONICAL_MANIFEST)
    assert "priorities.md" not in manifest
    assert "role.md" in manifest


def test_set_role_writes_canonical(project_with_library: Path) -> None:
    """set_role flow includes canonical write (wire-in verification)."""
    asm = Assembler(project_with_library)
    asm.set_role("test-role", provider_name="claude")

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    assert (canonical / "role.md").exists()
    assert (canonical / "traits" / "trait-alpha.md").exists()
    assert (canonical / CANONICAL_MANIFEST).exists()


# -- HATS-289: aggregator-in-canonical (`imports.md`) --


def test_imports_aggregator_exists_and_has_no_noise(project_with_library: Path) -> None:
    """imports.md is pure @import list — no markers, no headings, no boilerplate."""
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    aggregator = project_with_library / AGENT_DIR / CANONICAL_DIR / "imports.md"
    assert aggregator.exists()

    body = aggregator.read_text()
    assert "<!--" not in body
    assert "# ai-hats" not in body  # no heading
    # Every non-blank line is an @import.
    for line in body.splitlines():
        if line.strip():
            assert line.startswith("@./"), f"unexpected non-import line: {line!r}"


def test_imports_aggregator_section_order(project_with_library: Path) -> None:
    """Order: priorities → traits → role → rules → skills_index."""
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    body = (project_with_library / AGENT_DIR / CANONICAL_DIR / "imports.md").read_text()
    pos_priorities = body.index("@./priorities.md")
    pos_trait = body.index("@./traits/trait-alpha.md")
    pos_role = body.index("@./role.md")
    pos_rule = body.index("@./rules/rule_a.md")
    pos_skills = body.index("@./skills_index.md")
    assert pos_priorities < pos_trait < pos_role < pos_rule < pos_skills


def test_imports_aggregator_includes_user_rules(project_with_library: Path) -> None:
    """User-rules appear after framework rules so user wins on overlap."""
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    canonical = project_with_library / AGENT_DIR / CANONICAL_DIR
    user_rule = canonical / USER_RULES_SUBDIR / "my-conv.md"
    user_rule.write_text("# user rule\n")

    asm.write_canonical(result)  # second write picks up the new user-rule
    body = (canonical / "imports.md").read_text()

    assert f"@./{USER_RULES_SUBDIR}/my-conv.md" in body
    assert body.index("@./rules/rule_a.md") < body.index(f"@./{USER_RULES_SUBDIR}/my-conv.md")


def test_imports_aggregator_idempotent(project_with_library: Path) -> None:
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)
    aggregator = project_with_library / AGENT_DIR / CANONICAL_DIR / "imports.md"
    first_mtime = aggregator.stat().st_mtime_ns

    time.sleep(0.01)
    asm.write_canonical(result)
    assert aggregator.stat().st_mtime_ns == first_mtime


def test_imports_aggregator_in_manifest(project_with_library: Path) -> None:
    asm, result = _composer_result(project_with_library)
    asm.write_canonical(result)

    manifest = asm._read_canonical_manifest(
        project_with_library / AGENT_DIR / CANONICAL_DIR / CANONICAL_MANIFEST
    )
    assert "imports.md" in manifest
