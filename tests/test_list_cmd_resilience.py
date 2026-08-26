"""Tests for `ai-hats list` resilience when metadata.yaml or config.yaml is broken (HATS-1510)."""

from __future__ import annotations

import logging
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import PROJECT_CONFIG


def test_list_rules_ignores_a_leftover_sidecar(tmp_path, monkeypatch):
    """HATS-1836: rules are listed by name; a leftover metadata.yaml is never read.

    The sidecar here is both malformed AND carries a description — neither may
    reach the output, and neither may break the listing.
    """
    project = tmp_path / "project"
    project.mkdir()
    user_home = tmp_path / "user_home"
    user_home.mkdir()

    rule_dir = user_home / ".ai-hats" / "rules" / "leftover_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Leftover Rule\n")
    (rule_dir / "metadata.yaml").write_text("description: SENTINEL-1836: unquoted colon\n")

    monkeypatch.chdir(project)
    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home))

    (project / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: assistant\ndefault_role: ''\nlibrary_paths: []\n"
    )

    result = CliRunner().invoke(main, ["list", "rules"])

    assert result.exit_code == 0, f"list rules failed with {result.exit_code}: {result.output}"
    assert "leftover_rule" in result.output
    assert "SENTINEL-1836" not in result.output


def test_list_roles_survives_broken_config(tmp_path, monkeypatch, caplog):
    project = tmp_path / "project"
    project.mkdir()
    user_home = tmp_path / "user_home"
    user_home.mkdir()

    # Create a user role with broken config.yaml
    broken_role_dir = user_home / ".ai-hats" / "roles" / "broken_role"
    broken_role_dir.mkdir(parents=True)
    (broken_role_dir / "config.yaml").write_text("description: unquoted: colon string\n")

    monkeypatch.chdir(project)
    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home))

    (project / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: assistant\ndefault_role: ''\nlibrary_paths: []\n"
    )

    runner = CliRunner()
    with caplog.at_level(logging.WARNING):
        result = runner.invoke(main, ["list", "roles"])

    assert result.exit_code == 0, f"list roles failed with {result.exit_code}: {result.output}"
    assert "broken_role" in result.output
    assert "failed to load config at" in caplog.text or "broken_role" in caplog.text
