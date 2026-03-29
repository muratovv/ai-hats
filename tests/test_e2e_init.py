"""E2E tests for full system initialization flow via CLI."""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.library import LibraryResolver
from ai_hats.models import ComponentType


def _all_roles() -> list[str]:
    """Discover all roles from the built-in library."""
    from pathlib import Path

    builtin = Path(__file__).resolve().parent.parent / "src" / "ai_hats" / "libraries"
    resolver = LibraryResolver([builtin])
    return sorted(resolver.list_components(ComponentType.ROLE))


ALL_ROLES = _all_roles()


@pytest.fixture()
def cli_project(tmp_path, monkeypatch):
    """Clean project directory with chdir and CliRunner."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project, CliRunner()


def test_init_creates_project(cli_project):
    """ai-hats init --role <role> --provider claude creates all artifacts."""
    project, runner = cli_project

    result = runner.invoke(main, ["init", "--role", ALL_ROLES[0], "--provider", "claude"])

    assert result.exit_code == 0, result.output
    assert (project / "ai-hats.yaml").exists()
    assert (project / "profile.json").exists()
    assert (project / ".agent" / "rules").is_dir()
    assert (project / ".agent" / "skills").is_dir()
    assert (project / ".agent" / "backlog" / "tasks").is_dir()
    assert (project / "CLAUDE.md").exists()
    assert len((project / "CLAUDE.md").read_text()) > 100


@pytest.mark.parametrize("role", ALL_ROLES, ids=ALL_ROLES)
def test_set_all_roles(cli_project, role):
    """Every built-in role assembles without errors via CLI."""
    project, runner = cli_project

    # init first
    r = runner.invoke(main, ["init", "--provider", "claude"])
    assert r.exit_code == 0, r.output

    # set role
    r = runner.invoke(main, ["set", role, "--provider", "claude"])
    assert r.exit_code == 0, r.output
    assert "Warning" not in r.output
    assert (project / "CLAUDE.md").exists()
    assert len((project / "CLAUDE.md").read_text()) > 100


def test_status_after_set(cli_project):
    """ai-hats status shows role and components after set."""
    project, runner = cli_project

    runner.invoke(main, ["init", "--provider", "claude"])
    runner.invoke(main, ["set", ALL_ROLES[0], "--provider", "claude"])

    r = runner.invoke(main, ["status"])
    assert r.exit_code == 0, r.output
    assert ALL_ROLES[0] in r.output


def test_bump_after_set(cli_project):
    """ai-hats bump re-assembles without errors."""
    project, runner = cli_project

    runner.invoke(main, ["init", "--provider", "claude"])
    runner.invoke(main, ["set", ALL_ROLES[0], "--provider", "claude"])

    prompt_before = (project / "CLAUDE.md").read_text()

    r = runner.invoke(main, ["bump"])
    assert r.exit_code == 0, r.output
    assert "Bumped" in r.output

    prompt_after = (project / "CLAUDE.md").read_text()
    assert len(prompt_after) > 100
    assert prompt_before == prompt_after


def test_init_idempotent_via_cli(cli_project):
    """Repeated init does not break existing state."""
    project, runner = cli_project

    runner.invoke(main, ["init", "--role", ALL_ROLES[0], "--provider", "claude"])
    prompt_first = (project / "CLAUDE.md").read_text()

    r = runner.invoke(main, ["init", "--role", ALL_ROLES[0], "--provider", "claude"])
    assert r.exit_code == 0, r.output

    prompt_second = (project / "CLAUDE.md").read_text()
    assert prompt_first == prompt_second
