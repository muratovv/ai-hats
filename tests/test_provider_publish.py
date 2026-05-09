"""Tests for ClaudeProvider.publish (HATS-283)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_hats.providers import (
    PUBLISH_AGGREGATOR_END,
    PUBLISH_AGGREGATOR_START,
    PUBLISH_MANIFEST,
    ClaudeProvider,
    GeminiProvider,
)


@pytest.fixture
def canonical(tmp_path: Path) -> Path:
    """Build a populated `.agent/ai-hats/` canonical layer for publish to consume."""
    canonical = tmp_path / ".agent" / "ai-hats"
    canonical.mkdir(parents=True)

    (canonical / "priorities.md").write_text("# Priorities\n\n1. Reliability\n")
    (canonical / "role.md").write_text("# ROLE\n\nRole text.\n")
    (canonical / "skills_index.md").write_text("# Skills\n\n- **skill_x**\n")

    (canonical / "traits").mkdir()
    (canonical / "traits" / "trait-a.md").write_text("Trait A body.\n")
    (canonical / "traits" / "trait-b.md").write_text("Trait B body.\n")

    (canonical / "rules").mkdir()
    (canonical / "rules" / "rule_one.md").write_text("# rule_one\nDo one.\n")
    (canonical / "rules" / "rule_two.md").write_text("# rule_two\nDo two.\n")

    (canonical / "user-rules").mkdir()

    manifest = (
        "# ai-hats canonical layer manifest. Do not edit.\n"
        "priorities.md\n"
        "role.md\n"
        "rules/rule_one.md\n"
        "rules/rule_two.md\n"
        "skills_index.md\n"
        "traits/trait-a.md\n"
        "traits/trait-b.md\n"
    )
    (canonical / "MANAGED").write_text(manifest)
    return canonical


@pytest.fixture
def project(tmp_path: Path, canonical: Path) -> Path:
    return canonical.parent.parent


def test_publish_creates_aggregator_and_mirror(project: Path, canonical: Path) -> None:
    ClaudeProvider().publish(canonical, project)
    out = project / ".claude"

    assert (out / "CLAUDE.md").exists()
    assert (out / "priorities.md").exists()
    assert (out / "role.md").exists()
    assert (out / "skills_index.md").exists()
    assert (out / "traits" / "trait-a.md").exists()
    assert (out / "traits" / "trait-b.md").exists()
    assert (out / "rules" / "rule_one.md").exists()
    assert (out / "rules" / "rule_two.md").exists()
    assert (out / PUBLISH_MANIFEST).exists()


def test_publish_aggregator_imports_correct_order(project: Path, canonical: Path) -> None:
    ClaudeProvider().publish(canonical, project)
    body = (project / ".claude" / "CLAUDE.md").read_text()

    assert PUBLISH_AGGREGATOR_START in body
    assert PUBLISH_AGGREGATOR_END in body

    # Each section appears at most once.
    for line in (
        "@./priorities.md",
        "@./traits/trait-a.md",
        "@./traits/trait-b.md",
        "@./role.md",
        "@./rules/rule_one.md",
        "@./rules/rule_two.md",
        "@./skills_index.md",
    ):
        assert body.count(line) == 1

    # Section order: priorities < traits < role < rules < skills_index.
    pos_priorities = body.index("@./priorities.md")
    pos_trait_a = body.index("@./traits/trait-a.md")
    pos_role = body.index("@./role.md")
    pos_rule_one = body.index("@./rules/rule_one.md")
    pos_skills = body.index("@./skills_index.md")
    assert pos_priorities < pos_trait_a < pos_role < pos_rule_one < pos_skills


def test_publish_content_byte_identical_to_canonical(project: Path, canonical: Path) -> None:
    ClaudeProvider().publish(canonical, project)
    out = project / ".claude"

    for rel in (
        "priorities.md",
        "role.md",
        "skills_index.md",
        "traits/trait-a.md",
        "rules/rule_one.md",
    ):
        assert (out / rel).read_bytes() == (canonical / rel).read_bytes()


def test_publish_user_rules_alongside_framework(project: Path, canonical: Path) -> None:
    (canonical / "user-rules" / "my-conv.md").write_text("# project rule\n")
    ClaudeProvider().publish(canonical, project)

    out = project / ".claude"
    assert (out / "rules" / "my-conv.md").exists()
    assert (out / "rules" / "my-conv.md").read_text() == "# project rule\n"

    aggregator = (out / "CLAUDE.md").read_text()
    assert "@./rules/my-conv.md" in aggregator

    manifest = (out / PUBLISH_MANIFEST).read_text()
    assert "rules/my-conv.md" in manifest


def test_publish_idempotent_no_op(project: Path, canonical: Path) -> None:
    ClaudeProvider().publish(canonical, project)
    target = project / ".claude" / "rules" / "rule_one.md"
    first_mtime = target.stat().st_mtime_ns

    time.sleep(0.01)
    ClaudeProvider().publish(canonical, project)
    assert target.stat().st_mtime_ns == first_mtime


def test_publish_stale_cleanup(project: Path, canonical: Path) -> None:
    ClaudeProvider().publish(canonical, project)
    out = project / ".claude"
    assert (out / "rules" / "rule_one.md").exists()

    # Mutate canonical: drop rule_one.
    (canonical / "rules" / "rule_one.md").unlink()
    new_manifest = (
        "# ai-hats canonical layer manifest. Do not edit.\n"
        "priorities.md\n"
        "role.md\n"
        "rules/rule_two.md\n"
        "skills_index.md\n"
        "traits/trait-a.md\n"
        "traits/trait-b.md\n"
    )
    (canonical / "MANAGED").write_text(new_manifest)

    ClaudeProvider().publish(canonical, project)

    assert not (out / "rules" / "rule_one.md").exists()
    assert (out / "rules" / "rule_two.md").exists()

    aggregator = (out / "CLAUDE.md").read_text()
    assert "@./rules/rule_one.md" not in aggregator


def test_publish_preserves_user_files(project: Path, canonical: Path) -> None:
    out = project / ".claude"
    out.mkdir(exist_ok=True)
    user_file = out / "my-notes.md"
    user_file.write_text("# my notes\n")

    ClaudeProvider().publish(canonical, project)

    assert user_file.exists()
    assert user_file.read_text() == "# my notes\n"
    manifest = (out / PUBLISH_MANIFEST).read_text()
    assert "my-notes.md" not in manifest


def test_publish_preserves_existing_skills_dir(project: Path, canonical: Path) -> None:
    skills = project / ".claude" / "skills"
    skills.mkdir(parents=True)
    skill_file = skills / "my_skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# user skill\n")
    (skills / ".ai-hats-managed").write_text("my_skill\n")

    ClaudeProvider().publish(canonical, project)

    assert skill_file.exists()
    publish_manifest = (project / ".claude" / PUBLISH_MANIFEST).read_text()
    assert "skills/" not in publish_manifest


def test_gemini_publish_is_noop(project: Path, canonical: Path) -> None:
    GeminiProvider().publish(canonical, project)
    assert not (project / ".claude").exists()
    assert not (project / ".gemini").exists()


def test_publish_handles_missing_manifest(project: Path, canonical: Path) -> None:
    """Empty/missing canonical MANAGED → publish noops gracefully."""
    (canonical / "MANAGED").unlink()
    ClaudeProvider().publish(canonical, project)
    # Nothing should exist except possibly empty .claude/
    out = project / ".claude"
    assert not (out / "CLAUDE.md").exists()


def test_publish_cleans_when_canonical_emptied(project: Path, canonical: Path) -> None:
    """First publish populates; second with empty MANAGED removes published files."""
    ClaudeProvider().publish(canonical, project)
    out = project / ".claude"
    assert (out / "rules" / "rule_one.md").exists()

    # Empty canonical.
    (canonical / "MANAGED").write_text("")
    ClaudeProvider().publish(canonical, project)

    assert not (out / "rules" / "rule_one.md").exists()
    assert not (out / "CLAUDE.md").exists()


def test_set_role_publishes_canonical(tmp_path: Path) -> None:
    """Integration: Assembler.set_role triggers ClaudeProvider.publish."""
    from ai_hats.assembler import Assembler

    proj = tmp_path / "proj"
    proj.mkdir()
    lib = proj / "libraries"

    rule_dir = lib / "rules" / "rule_x"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# rule_x\n")
    (rule_dir / "metadata.yaml").write_text("name: rule_x\n")

    trait_dir = lib / "traits" / "trait-x"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-x\ncomposition:\n  rules:\n    - rule_x\ninjection: |\n  Trait X.\n"
    )

    role_dir = lib / "roles" / "r1"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: r1\npriorities:\n  - Reliability\ncomposition:\n  traits:\n    - trait-x\n"
        "injection: |\n  Role text.\n"
    )

    (proj / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(proj)
    asm.set_role("r1", provider_name="claude")

    out = proj / ".claude"
    assert (out / "CLAUDE.md").exists()
    aggregator = (out / "CLAUDE.md").read_text()
    assert "@./role.md" in aggregator
    assert "@./traits/trait-x.md" in aggregator
    assert "@./rules/rule_x.md" in aggregator
    assert (out / PUBLISH_MANIFEST).exists()
