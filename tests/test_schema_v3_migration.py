"""Tests for schema v2→v3 migration + removed `self migrate` CLI (HATS-285)."""

from __future__ import annotations

import time
from pathlib import Path

import yaml
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats.providers import (
    INJECTION_END,
    INJECTION_START,
    PUBLISH_AGGREGATOR_END,
    PUBLISH_AGGREGATOR_START,
    ClaudeProvider,
)


def test_schema_default_is_v3() -> None:
    cfg = ProjectConfig()
    assert cfg.schema_version == 3


def test_from_yaml_bumps_v2_to_v3(tmp_path: Path) -> None:
    yaml_path = tmp_path / "ai-hats.yaml"
    yaml_path.write_text("schema_version: 2\nprovider: claude\n")

    cfg = ProjectConfig.from_yaml(yaml_path)
    assert cfg.schema_version == 3


def test_from_yaml_v1_chained_through_v3(tmp_path: Path) -> None:
    yaml_path = tmp_path / "ai-hats.yaml"
    yaml_path.write_text("schema_version: 1\nprovider: claude\n")

    cfg = ProjectConfig.from_yaml(yaml_path)
    assert cfg.schema_version == 3


def test_migrate_claude_md_strips_legacy_block(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    legacy = f"{INJECTION_START}\n[old huge inline blob]\n{INJECTION_END}\n"
    (project / "CLAUDE.md").write_text(legacy)
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    asm._migrate_claude_md_to_v3(ClaudeProvider())

    body = (project / "CLAUDE.md").read_text()
    assert INJECTION_START not in body
    assert INJECTION_END not in body
    assert PUBLISH_AGGREGATOR_START in body
    assert PUBLISH_AGGREGATOR_END in body
    assert "@./.claude/CLAUDE.md" in body


def test_migrate_claude_md_idempotent_on_lowercase(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    scaffold = f"{PUBLISH_AGGREGATOR_START}\n@./.claude/CLAUDE.md\n{PUBLISH_AGGREGATOR_END}\n"
    (project / "CLAUDE.md").write_text(scaffold)
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    first_mtime = (project / "CLAUDE.md").stat().st_mtime_ns
    time.sleep(0.01)
    asm._migrate_claude_md_to_v3(ClaudeProvider())

    assert (project / "CLAUDE.md").stat().st_mtime_ns == first_mtime
    assert (project / "CLAUDE.md").read_text() == scaffold


def test_migrate_claude_md_no_file_creates_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    asm._migrate_claude_md_to_v3(ClaudeProvider())

    body = (project / "CLAUDE.md").read_text()
    assert PUBLISH_AGGREGATOR_START in body
    assert "@./.claude/CLAUDE.md" in body


def test_migrate_claude_md_preserves_user_content_around_markers(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    legacy = f"# My Project\n\n{INJECTION_START}\n[old blob]\n{INJECTION_END}\n\nMore notes.\n"
    (project / "CLAUDE.md").write_text(legacy)
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    asm._migrate_claude_md_to_v3(ClaudeProvider())

    body = (project / "CLAUDE.md").read_text()
    assert "# My Project" in body
    assert "More notes." in body
    assert "[old blob]" not in body
    assert PUBLISH_AGGREGATOR_START in body
    # Order: project header, scaffold, user notes.
    assert body.index("# My Project") < body.index(PUBLISH_AGGREGATOR_START)
    assert body.index(PUBLISH_AGGREGATOR_END) < body.index("More notes.")


def test_migrate_claude_md_no_markers_prepends_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    user_only = "# Pure user content\n\nNo ai-hats markers anywhere.\n"
    (project / "CLAUDE.md").write_text(user_only)
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    asm._migrate_claude_md_to_v3(ClaudeProvider())

    body = (project / "CLAUDE.md").read_text()
    assert body.startswith(PUBLISH_AGGREGATOR_START)
    assert "# Pure user content" in body
    assert "No ai-hats markers anywhere." in body
    assert body.index(PUBLISH_AGGREGATOR_END) < body.index("# Pure user content")


def test_init_on_legacy_project_migrates(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    legacy = f"{INJECTION_START}\n[old]\n{INJECTION_END}\n"
    (project / "CLAUDE.md").write_text(legacy)

    Assembler(project).init(provider="claude")

    body = (project / "CLAUDE.md").read_text()
    assert INJECTION_START not in body
    assert PUBLISH_AGGREGATOR_START in body


def test_set_role_on_legacy_project_migrates(tmp_path: Path) -> None:
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
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")
    legacy = f"{INJECTION_START}\n[old]\n{INJECTION_END}\n"
    (project / "CLAUDE.md").write_text(legacy)

    Assembler(project).set_role("r1", provider_name="claude")

    body = (project / "CLAUDE.md").read_text()
    assert INJECTION_START not in body
    assert PUBLISH_AGGREGATOR_START in body
    aggregator = (project / ".claude" / "CLAUDE.md").read_text()
    assert "@./role.md" in aggregator


def test_bump_migrates_then_assembles(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    lib = project / "libraries"
    role_dir = lib / "roles" / "r1"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: r1\npriorities:\n  - Reliability\ninjection: |\n  Role X.\n"
    )
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\nactive_role: r1\n")
    legacy = f"{INJECTION_START}\n[old huge content]\n{INJECTION_END}\n"
    (project / "CLAUDE.md").write_text(legacy)

    Assembler(project).bump()

    body = (project / "CLAUDE.md").read_text()
    assert INJECTION_START not in body
    assert PUBLISH_AGGREGATOR_START in body
    assert "@./.claude/CLAUDE.md" in body


def test_obsolete_files_cleanup_in_bump(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".agent").mkdir()
    legacy_md = project / ".agent" / "backlog.md"
    legacy_md.write_text("# obsolete\n")
    (project / "ai-hats.yaml").write_text("schema_version: 2\nprovider: claude\n")

    asm = Assembler(project)
    asm.bump()  # no active role, but cleanup still runs

    assert not legacy_md.exists()


def test_migrate_cli_command_removed(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    runner = CliRunner()
    result = runner.invoke(main, ["self", "migrate"])
    assert result.exit_code != 0
    assert "No such command" in (result.output or "") or "Usage:" in (result.output or "")


def test_to_dict_emits_v3(tmp_path: Path) -> None:
    cfg = ProjectConfig(provider="claude")
    yaml_path = tmp_path / "ai-hats.yaml"
    cfg.save(yaml_path)
    raw = yaml.safe_load(yaml_path.read_text())
    assert raw["schema_version"] == 3
