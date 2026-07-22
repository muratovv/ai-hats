"""Claude Code path conventions — single home for ``.claude/*`` coupling (HATS-907/908).

Each literal appears once; Path / rel-string forms derive from it. Frozen
historical strings (legacy heal targets) stay at call sites — they must NOT
move when the live layout does.
"""

from __future__ import annotations

from pathlib import Path


AI_HATS_MANAGED_MARKER = ".ai-hats-managed"

_CLAUDE_DIRNAME = ".claude"
_CLAUDE_SKILLS_DIRNAME = "skills"

# Project-relative string forms — migration allowlists (backup scope, healer /
# asserter settings targets) match project-relative strings, not Paths.
CLAUDE_SETTINGS_JSON_REL = f"{_CLAUDE_DIRNAME}/settings.json"
CLAUDE_SETTINGS_LOCAL_JSON_REL = f"{_CLAUDE_DIRNAME}/settings.local.json"
CLAUDE_MD_FILENAME = "CLAUDE.md"


def claude_dir(base: Path) -> Path:
    """Claude Code's config dir under ``base``: ``.claude/``."""
    return base / _CLAUDE_DIRNAME


def claude_skills_dir(base: Path) -> Path:
    """Claude Code's skill auto-discovery dir under ``base``: ``.claude/skills/``.

    ``base`` is a project root or the user home — Claude Code scans both
    scopes (HATS-901/907).
    """
    return claude_dir(base) / _CLAUDE_SKILLS_DIRNAME


def claude_settings_json(base: Path) -> Path:
    """Claude Code's project settings file: ``.claude/settings.json``."""
    return base / CLAUDE_SETTINGS_JSON_REL


def claude_settings_local_json(base: Path) -> Path:
    """Claude Code's local (untracked) settings file: ``.claude/settings.local.json``."""
    return base / CLAUDE_SETTINGS_LOCAL_JSON_REL


def claude_user_settings_json() -> Path:
    """Claude Code's user-global settings file (HATS-1006)."""
    from ._discovery import tool_home

    return tool_home("claude", "CLAUDE_CONFIG_DIR") / "settings.json"


def claude_md(project_dir: Path) -> Path:
    """Claude Code's memory / system-prompt file: ``./CLAUDE.md``."""
    return project_dir / CLAUDE_MD_FILENAME


def _project_key(project_dir: Path) -> str:
    """Claude Code's transcript-dir key: absolute project path, ``/`` → ``-``."""
    return str(project_dir).replace("/", "-")


def claude_transcripts_dir(project_dir: Path) -> Path:
    """Dir of Claude Code's conversation JSONLs: ``~/.claude/projects/<key>/``."""
    from ._discovery import tool_home

    return tool_home("claude", "CLAUDE_CONFIG_DIR") / "projects" / _project_key(project_dir)


def claude_transcript_path(project_dir: Path, claude_session_id: str) -> Path:
    """One session's conversation JSONL under :func:`claude_transcripts_dir`."""
    return claude_transcripts_dir(project_dir) / f"{claude_session_id}.jsonl"


def claude_plugin_manifest_dir(plugin_root: Path) -> Path:
    """Claude Code plugin-manifest dir: ``<plugin>/.claude-plugin/``."""
    return plugin_root / ".claude-plugin"


def claude_plugin_manifest(plugin_root: Path) -> Path:
    """Claude Code plugin manifest: ``<plugin>/.claude-plugin/plugin.json``."""
    return claude_plugin_manifest_dir(plugin_root) / "plugin.json"


def claude_plugin_skills_dir(plugin_root: Path) -> Path:
    """Skill auto-discovery dir inside a plugin: ``<plugin>/skills/``."""
    return plugin_root / _CLAUDE_SKILLS_DIRNAME


# Expanded by Claude Code at hook-exec time to the project root; migration
# callers strip it for static path resolution (HATS-549 Q.1: single source).
CLAUDE_PROJECT_DIR_VAR: str = "$CLAUDE_PROJECT_DIR/"


def strip_claude_project_dir(s: str) -> str:
    """Remove a leading ``$CLAUDE_PROJECT_DIR/`` placeholder if present.

    Idempotent; non-prefixed strings pass through unchanged. Use when
    converting a hook command value into a project-relative path for
    on-disk existence checks.
    """
    if s.startswith(CLAUDE_PROJECT_DIR_VAR):
        return s[len(CLAUDE_PROJECT_DIR_VAR) :]
    return s


__all__ = [
    "AI_HATS_MANAGED_MARKER",
    "CLAUDE_MD_FILENAME",
    "CLAUDE_PROJECT_DIR_VAR",
    "CLAUDE_SETTINGS_JSON_REL",
    "CLAUDE_SETTINGS_LOCAL_JSON_REL",
    "claude_dir",
    "claude_md",
    "claude_plugin_manifest",
    "claude_plugin_manifest_dir",
    "claude_plugin_skills_dir",
    "claude_settings_json",
    "claude_settings_local_json",
    "claude_skills_dir",
    "claude_user_settings_json",
    "claude_transcript_path",
    "claude_transcripts_dir",
    "strip_claude_project_dir",
]
