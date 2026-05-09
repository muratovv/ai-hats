"""Tests for scaffold-as-asset (HATS-284)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.providers import (
    INJECTION_END,
    INJECTION_START,
    PUBLISH_AGGREGATOR_END,
    PUBLISH_AGGREGATOR_START,
    ClaudeProvider,
)


def _builtin_template_path() -> Path:
    """Path to the builtin Claude scaffold template inside the package."""
    import ai_hats

    return (
        Path(ai_hats.__file__).parent / "libraries" / "templates" / "claude" / "CLAUDE.md.template"
    )


def test_scaffold_template_asset_exists() -> None:
    """Builtin Claude template ships with the package and has both markers."""
    template = _builtin_template_path()
    assert template.is_file(), f"missing builtin template at {template}"

    body = template.read_text()
    assert PUBLISH_AGGREGATOR_START in body
    assert PUBLISH_AGGREGATOR_END in body
    assert "@./.claude/CLAUDE.md" in body
    # No legacy uppercase markers in target template.
    assert INJECTION_START not in body
    assert INJECTION_END not in body


def test_resolve_scaffold_template_finds_builtin(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    resolved = asm._resolve_scaffold_template("templates/claude/CLAUDE.md.template")
    assert resolved is not None
    assert resolved.is_file()
    assert resolved.read_text() == _builtin_template_path().read_text()


def test_resolve_scaffold_template_project_override(tmp_path: Path) -> None:
    """Project-local libraries/ wins over builtin (last-wins resolver order)."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    override = project / "libraries" / "templates" / "claude" / "CLAUDE.md.template"
    override.parent.mkdir(parents=True)
    override.write_text("# project override\n@./.claude/CLAUDE.md\n")

    asm = Assembler(project)
    resolved = asm._resolve_scaffold_template("templates/claude/CLAUDE.md.template")
    assert resolved == override


def test_init_writes_claude_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    Assembler(project).init(provider="claude")

    claude_md = project / "CLAUDE.md"
    assert claude_md.exists()
    body = claude_md.read_text()
    assert PUBLISH_AGGREGATOR_START in body
    assert PUBLISH_AGGREGATOR_END in body
    assert "@./.claude/CLAUDE.md" in body
    # No legacy uppercase from update_system_prompt.
    assert INJECTION_START not in body
    assert INJECTION_END not in body


def test_init_preserves_existing_claude_md(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    pre_existing = "# my project\n\nNotes.\n"
    (project / "CLAUDE.md").write_text(pre_existing)

    Assembler(project).init(provider="claude")
    assert (project / "CLAUDE.md").read_text() == pre_existing


def test_init_gemini_no_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    Assembler(project).init(provider="gemini")

    assert not (project / "GEMINI.md").exists()
    assert not (project / "CLAUDE.md").exists()


def test_update_system_prompt_skips_on_lowercase_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    scaffold = f"{PUBLISH_AGGREGATOR_START}\n@./.claude/CLAUDE.md\n{PUBLISH_AGGREGATOR_END}\n"
    (project / "CLAUDE.md").write_text(scaffold)

    ClaudeProvider().update_system_prompt(project, "BIG INLINE BLOB")
    # Body unchanged.
    assert (project / "CLAUDE.md").read_text() == scaffold


def test_update_system_prompt_legacy_uppercase_unchanged(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    legacy = f"{INJECTION_START}\nold\n{INJECTION_END}\n"
    (project / "CLAUDE.md").write_text(legacy)

    ClaudeProvider().update_system_prompt(project, "new")
    body = (project / "CLAUDE.md").read_text()
    assert "new" in body
    assert "old" not in body
    assert INJECTION_START in body and INJECTION_END in body


def test_init_then_set_role_no_double_blocks(tmp_path: Path) -> None:
    """init+set_role gives scaffold only — no uppercase block coexisting."""
    project = tmp_path / "proj"
    project.mkdir()

    lib = project / "libraries"
    rule_dir = lib / "rules" / "rule_x"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# rule_x\n")
    (rule_dir / "metadata.yaml").write_text("name: rule_x\n")

    role_dir = lib / "roles" / "r1"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: r1\npriorities:\n  - Reliability\n"
        "composition:\n  rules:\n    - rule_x\ninjection: |\n  Role X.\n"
    )

    Assembler(project).init(role="r1", provider="claude")

    body = (project / "CLAUDE.md").read_text()
    assert PUBLISH_AGGREGATOR_START in body
    assert PUBLISH_AGGREGATOR_END in body
    assert "@./.claude/CLAUDE.md" in body
    assert INJECTION_START not in body
    assert INJECTION_END not in body

    # T3 publish still runs.
    aggregator = project / ".claude" / "CLAUDE.md"
    assert aggregator.exists()
    assert "@./role.md" in aggregator.read_text()


def test_set_role_on_legacy_project_unchanged(tmp_path: Path) -> None:
    """Project with only uppercase markers continues legacy update flow."""
    project = tmp_path / "proj"
    project.mkdir()

    # Pre-existing legacy CLAUDE.md (uppercase markers, no lowercase).
    legacy = f"{INJECTION_START}\nold inline content\n{INJECTION_END}\n"
    (project / "CLAUDE.md").write_text(legacy)

    lib = project / "libraries"
    role_dir = lib / "roles" / "r1"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: r1\npriorities:\n  - Reliability\ninjection: |\n  Role X.\n"
    )
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    asm.set_role("r1", provider_name="claude")

    body = (project / "CLAUDE.md").read_text()
    assert INJECTION_START in body
    assert INJECTION_END in body
    assert "Role X." in body  # uppercase block updated
    assert "old inline content" not in body
    # No lowercase scaffold introduced — that's T5 migration's job.
    assert PUBLISH_AGGREGATOR_START not in body


@pytest.mark.parametrize("idem_calls", [1, 2, 3])
def test_ensure_scaffold_idempotent(tmp_path: Path, idem_calls: int) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    for _ in range(idem_calls):
        asm._ensure_scaffold(ClaudeProvider())

    # Final file is the template, not duplicated.
    body = (project / "CLAUDE.md").read_text()
    assert body.count(PUBLISH_AGGREGATOR_START) == 1
    assert body.count(PUBLISH_AGGREGATOR_END) == 1
