"""Tool-layout contract: ``paths.claude`` / ``paths.agy`` emit the exact
on-disk literals Claude Code / Agy CLI use (HATS-908).

The ONE place tests may duplicate these literals — every other test builds
tool paths via the helpers, so a typo in a helper fails here and only here.
"""

from pathlib import Path

from ai_hats.paths import (
    AI_HATS_MANAGED_MARKER,
    CLAUDE_MD_FILENAME,
    CLAUDE_PROJECT_DIR_VAR,
    CLAUDE_SETTINGS_JSON_REL,
    CLAUDE_SETTINGS_LOCAL_JSON_REL,
    GEMINI_MD_FILENAME,
    claude_dir,
    claude_md,
    claude_plugin_manifest,
    claude_plugin_manifest_dir,
    claude_plugin_skills_dir,
    claude_settings_json,
    claude_settings_local_json,
    claude_skills_dir,
    claude_transcript_path,
    claude_transcripts_dir,
    gemini_md,
    agy_skills_dir,
    strip_claude_project_dir,
)


def test_claude_dir_layout(tmp_path):
    assert claude_dir(tmp_path) == tmp_path / ".claude"
    assert claude_skills_dir(tmp_path) == tmp_path / ".claude" / "skills"
    assert claude_settings_json(tmp_path) == tmp_path / ".claude" / "settings.json"
    assert claude_settings_local_json(tmp_path) == tmp_path / ".claude" / "settings.local.json"


def test_claude_settings_relpaths_are_project_relative_strings():
    assert CLAUDE_SETTINGS_JSON_REL == ".claude/settings.json"
    assert CLAUDE_SETTINGS_LOCAL_JSON_REL == ".claude/settings.local.json"


def test_claude_memory_file(tmp_path):
    assert CLAUDE_MD_FILENAME == "CLAUDE.md"
    assert claude_md(tmp_path) == tmp_path / "CLAUDE.md"


def test_claude_project_dir_var_contract():
    assert CLAUDE_PROJECT_DIR_VAR == "$CLAUDE_PROJECT_DIR/"
    assert strip_claude_project_dir("$CLAUDE_PROJECT_DIR/hooks/x.py") == "hooks/x.py"
    assert strip_claude_project_dir("hooks/x.py") == "hooks/x.py"


def test_claude_transcripts_location(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = Path("/Users/alice/dev/proj")
    transcripts = tmp_path / ".claude" / "projects" / "-Users-alice-dev-proj"
    assert claude_transcripts_dir(project) == transcripts
    assert claude_transcript_path(project, "abc-123") == transcripts / "abc-123.jsonl"


def test_claude_plugin_layout(tmp_path):
    assert claude_plugin_manifest_dir(tmp_path) == tmp_path / ".claude-plugin"
    assert claude_plugin_manifest(tmp_path) == tmp_path / ".claude-plugin" / "plugin.json"
    assert claude_plugin_skills_dir(tmp_path) == tmp_path / "skills"


def test_managed_marker_name():
    assert AI_HATS_MANAGED_MARKER == ".ai-hats-managed"


def test_agy_layout(tmp_path):
    assert GEMINI_MD_FILENAME == "GEMINI.md"
    assert gemini_md(tmp_path) == tmp_path / "GEMINI.md"
    assert agy_skills_dir(tmp_path) == tmp_path / ".agy" / "skills"
