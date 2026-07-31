"""Tool-layout contract: ``paths.claude`` / ``paths.agy`` emit the exact
on-disk literals Claude Code / Agy CLI use (HATS-908).

The ONE place tests may duplicate these literals — every other test builds
tool paths via the helpers, so a typo in a helper fails here and only here.
"""

from pathlib import Path

import pytest

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


# HATS-1412: real (project path, Claude-Code-created dir) pairs, sampled from
# ~/.claude/projects/ and cross-checked against each transcript's own "cwd".
REAL_PROJECT_KEY_PAIRS = [
    # The exact live repro this task was opened from.
    (
        "/private/var/folders/q5/_t4msh1j5yjfqkrq8w4xx5x00000gn/T/ai-hats-wt-task-hats-1402-6n008_h6",
        "-private-var-folders-q5--t4msh1j5yjfqkrq8w4xx5x00000gn-T-ai-hats-wt-task-hats-1402-6n008-h6",
    ),
    # A second real sample with two underscores (tempfile.mkdtemp suffix),
    # confirming the rule generalizes beyond the single-underscore repro.
    (
        "/private/var/folders/q5/_t4msh1j5yjfqkrq8w4xx5x00000gn/T"
        "/ai-hats-wt-agent-exp-agent-20260719-110108-1-_ky_i3th",
        "-private-var-folders-q5--t4msh1j5yjfqkrq8w4xx5x00000gn-T"
        "-ai-hats-wt-agent-exp-agent-20260719-110108-1--ky-i3th",
    ),
]


@pytest.mark.parametrize("project_path,expected_key", REAL_PROJECT_KEY_PAIRS)
def test_claude_transcripts_dir_matches_real_claude_code_slug(
    tmp_path, monkeypatch, project_path, expected_key
):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        claude_transcripts_dir(Path(project_path))
        == tmp_path / ".claude" / "projects" / expected_key
    )


@pytest.mark.parametrize(
    "project_path,expected_key",
    [
        ("/Users/alice/dev/proj", "-Users-alice-dev-proj"),
        ("/Users/alice/dev/my_proj", "-Users-alice-dev-my-proj"),
        ("/Users/alice/dev/my.proj v2", "-Users-alice-dev-my-proj-v2"),
        ("/Users/alice/dev/a_b.c d-e", "-Users-alice-dev-a-b-c-d-e"),
    ],
)
def test_claude_transcripts_dir_synthetic_special_chars(
    tmp_path, monkeypatch, project_path, expected_key
):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        claude_transcripts_dir(Path(project_path))
        == tmp_path / ".claude" / "projects" / expected_key
    )


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
