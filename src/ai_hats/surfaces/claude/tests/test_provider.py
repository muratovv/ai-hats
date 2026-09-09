"""ClaudeSurface specific tests."""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

from ai_hats.constants import PUBLISH_AGGREGATOR_END, PUBLISH_AGGREGATOR_START
from ai_hats.surfaces.claude.provider import ClaudeSurface, INJECTION_START, INJECTION_END


def test_build_full_content_no_splicing_root_claude_md(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()

    provider = ClaudeSurface()

    # Existing root CLAUDE.md must NOT be spliced into prompt.md
    existing_content = (
        f"Header\n{PUBLISH_AGGREGATOR_START}\nOld stuff\n{PUBLISH_AGGREGATOR_END}\nFooter"
    )
    system_prompt = project / "CLAUDE.md"
    system_prompt.parent.mkdir(parents=True, exist_ok=True)
    system_prompt.write_text(existing_content)

    full_content = provider._build_full_content(ProjectLayout.at(project), "New Prompt Content")

    assert full_content == f"{INJECTION_START}\nNew Prompt Content\n{INJECTION_END}\n"
    assert "Header" not in full_content


def test_build_full_content_clean_wrapping(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()

    provider = ClaudeSurface()

    full_content = provider._build_full_content(ProjectLayout.at(project), "New Prompt Content")
    assert full_content == f"{INJECTION_START}\nNew Prompt Content\n{INJECTION_END}\n"


def test_engine_returns_claude_engine():
    provider = ClaudeSurface()
    assert provider.supports_sdk_engine() is True
    assert provider.supports_session_command_wrappers() is True
    assert provider.engine() is not None


def test_claude_provider_system_prompt_path_is_none(tmp_path: Path):
    provider = ClaudeSurface()
    assert provider.system_prompt_path(ProjectLayout.at(tmp_path)) is None
    assert provider.update_system_prompt(ProjectLayout.at(tmp_path), "content") is None
