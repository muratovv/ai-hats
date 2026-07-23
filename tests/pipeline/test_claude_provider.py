"""ClaudeProvider specific tests."""

from __future__ import annotations

from pathlib import Path

from ai_hats.surfaces.claude.provider import ClaudeProvider, INJECTION_START, INJECTION_END, PUBLISH_AGGREGATOR_START, PUBLISH_AGGREGATOR_END


def test_build_full_content_legacy_markers(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    
    provider = ClaudeProvider()
    
    # Setup legacy markers
    existing_content = f"Header\n{PUBLISH_AGGREGATOR_START}\nOld stuff\n{PUBLISH_AGGREGATOR_END}\nFooter"
    system_prompt = provider.system_prompt_path(project)
    system_prompt.parent.mkdir(parents=True, exist_ok=True)
    system_prompt.write_text(existing_content)
    
    full_content = provider._build_full_content(project, "New Prompt Content")
    
    assert "Header\n" in full_content
    assert INJECTION_START in full_content
    assert "New Prompt Content" in full_content
    assert INJECTION_END in full_content
    assert "\nFooter" in full_content
    assert PUBLISH_AGGREGATOR_START not in full_content


def test_build_full_content_lowercase_markers(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    
    provider = ClaudeProvider()
    
    existing_content = f"Header\n{INJECTION_START}\nOld stuff\n{INJECTION_END}\nFooter"
    system_prompt = provider.system_prompt_path(project)
    system_prompt.parent.mkdir(parents=True, exist_ok=True)
    system_prompt.write_text(existing_content)
    
    full_content = provider._build_full_content(project, "New Prompt Content")
    
    assert "Header\n" in full_content
    assert INJECTION_START in full_content
    assert "New Prompt Content" in full_content
    assert INJECTION_END in full_content
    assert "\nFooter" in full_content


def test_build_full_content_no_markers(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    
    provider = ClaudeProvider()
    
    existing_content = "Header\nJust regular text\nFooter"
    system_prompt = provider.system_prompt_path(project)
    system_prompt.parent.mkdir(parents=True, exist_ok=True)
    system_prompt.write_text(existing_content)
    
    full_content = provider._build_full_content(project, "New Prompt Content")
    
    # Fallback when no markers present is to wrap in markers
    assert full_content == f"{INJECTION_START}\nNew Prompt Content\n{INJECTION_END}\n"


def test_engine_returns_claude_engine():
    provider = ClaudeProvider()
    engine = provider.engine()
    assert engine is not None
    assert engine.__class__.__name__ == "ClaudeSubagentEngine"
