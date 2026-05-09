"""Tests for the lazy-loaded routing.md provider publishing path (HATS-264)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.providers import ClaudeProvider, GeminiProvider, Provider


@pytest.fixture
def project_and_canonical(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "proj"
    project.mkdir()
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    return project, canonical


@pytest.mark.parametrize("provider_cls", [ClaudeProvider, GeminiProvider])
def test_export_routing_copies_canonical_file(
    project_and_canonical: tuple[Path, Path],
    provider_cls: type[Provider],
) -> None:
    project, canonical = project_and_canonical
    (canonical / "routing.md").write_text("# Skill Routing\n\n| t | s |\n|---|---|\n| x | y |\n")

    provider = provider_cls()
    provider.export_routing(project, canonical)

    target = provider.routing_export_path(project)
    assert target.exists()
    assert target.read_text().startswith("# Skill Routing")
    # Must live next to skills/, not inside it.
    assert target.parent == provider.skills_export_dir(project).parent


@pytest.mark.parametrize("provider_cls", [ClaudeProvider, GeminiProvider])
def test_export_routing_removes_target_when_canonical_absent(
    project_and_canonical: tuple[Path, Path],
    provider_cls: type[Provider],
) -> None:
    project, canonical = project_and_canonical
    provider = provider_cls()
    target = provider.routing_export_path(project)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("stale content\n")

    # Canonical has no routing.md.
    provider.export_routing(project, canonical)

    assert not target.exists()


@pytest.mark.parametrize("provider_cls", [ClaudeProvider, GeminiProvider])
def test_cleanup_routing_removes_published_file(
    project_and_canonical: tuple[Path, Path],
    provider_cls: type[Provider],
) -> None:
    project, _ = project_and_canonical
    provider = provider_cls()
    target = provider.routing_export_path(project)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# routing\n")

    provider.cleanup_routing(project)
    assert not target.exists()

    # Idempotent — cleanup on missing target does not raise.
    provider.cleanup_routing(project)


def test_routing_export_path_locations() -> None:
    project = Path("/proj")
    assert ClaudeProvider().routing_export_path(project) == Path("/proj/.claude/routing.md")
    assert GeminiProvider().routing_export_path(project) == Path("/proj/.gemini/routing.md")
