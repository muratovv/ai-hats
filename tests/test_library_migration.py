"""Library-class layout migration: `.agent/{rules,skills,hooks}` → `<dir>/library/...` (HATS-314)."""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.paths import rules_dir, skills_dir, user_hooks_dir


def _seed_library_legacy(project_dir: Path) -> dict[str, Path]:
    (project_dir / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    seeds: dict[str, Path] = {}
    # Rules: dir per rule with rule.md + metadata.yaml
    r = project_dir / ".agent" / "rules" / "my-rule"
    r.mkdir(parents=True)
    (r / "rule.md").write_text("# Rule")
    (r / "metadata.yaml").write_text("name: my-rule\n")
    seeds["rule"] = r / "rule.md"
    # Skills: dir per skill with SKILL.md
    s = project_dir / ".agent" / "skills" / "my-skill"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text("# Skill")
    seeds["skill"] = s / "SKILL.md"
    # Hooks: flat scripts
    h = project_dir / ".agent" / "hooks"
    h.mkdir(parents=True)
    (h / "pre-commit.sh").write_text("#!/bin/sh\necho hi")
    seeds["hook"] = h / "pre-commit.sh"
    return seeds


def test_library_migration_moves_all_paths(tmp_path: Path) -> None:
    _seed_library_legacy(tmp_path)
    asm = Assembler(tmp_path)

    asm._migrate_layout_v4_library()

    assert (rules_dir(tmp_path) / "my-rule" / "rule.md").exists()
    assert (rules_dir(tmp_path) / "my-rule" / "metadata.yaml").exists()
    assert (skills_dir(tmp_path) / "my-skill" / "SKILL.md").exists()
    # HATS-549 Phase 4: legacy hooks are partitioned by basename whitelist.
    # `pre-commit.sh` is NOT an ai-hats-owned hook → routes to `user-hooks/`,
    # keeping user-owned content out of the managed `library/hooks/` namespace.
    assert (user_hooks_dir(tmp_path) / "pre-commit.sh").exists()
    # Legacy gone
    for sub in ("rules", "skills", "hooks"):
        assert not (tmp_path / ".agent" / sub).exists(), f".agent/{sub} still present"


def test_library_migration_idempotent(tmp_path: Path) -> None:
    _seed_library_legacy(tmp_path)
    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_library()
    asm._migrate_layout_v4_library()  # no-op
    assert (rules_dir(tmp_path) / "my-rule" / "rule.md").exists()


def test_library_migration_preserves_claude_skills(tmp_path: Path) -> None:
    """`.claude/skills/` is external — migration must NOT touch it."""
    _seed_library_legacy(tmp_path)
    # Seed a claude-side skill that should survive untouched.
    cs = tmp_path / ".claude" / "skills" / "external"
    cs.mkdir(parents=True)
    (cs / "SKILL.md").write_text("# External skill")

    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_library()

    assert (cs / "SKILL.md").exists()
    assert (cs / "SKILL.md").read_text() == "# External skill"


def test_library_migration_noop_on_clean_project(tmp_path: Path) -> None:
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_library()  # must not raise
