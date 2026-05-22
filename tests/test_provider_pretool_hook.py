"""HATS-437 — ClaudeProvider.ensure_runtime_hooks PreToolUse autowire.

Covers:
    - fresh write into .claude/settings.json
    - idempotency on double-apply
    - preservation of pre-existing user-authored PreToolUse entries
    - skip when user already wired the same hook manually
    - update-in-place when the managed entry changes (e.g. hook path moved)
    - Gemini provider is a no-op (does not touch settings.json)
    - malformed / non-object JSON: leave alone (no clobber)
"""

import json
from pathlib import Path

import pytest

from ai_hats.providers import ClaudeProvider, GeminiProvider


SETTINGS = Path(".claude") / "settings.json"
EXPECTED_REL = ".agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh"


def _settings(project: Path) -> dict:
    return json.loads((project / SETTINGS).read_text())


def test_claude_writes_fresh_settings(tmp_path: Path) -> None:
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    data = _settings(tmp_path)
    entries = data["hooks"]["PreToolUse"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["matcher"] == "Bash"
    assert entry["_ai_hats_managed"] == "ai-hats:hats-437"
    assert entry["hooks"] == [{"type": "command", "command": EXPECTED_REL}]


def test_claude_double_apply_is_idempotent(tmp_path: Path) -> None:
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    first = _settings(tmp_path)
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    second = _settings(tmp_path)
    assert first == second
    assert len(second["hooks"]["PreToolUse"]) == 1


def test_claude_preserves_user_authored_entries(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / SETTINGS).write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "user/own.sh"}],
                        }
                    ]
                },
            }
        )
    )

    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    data = _settings(tmp_path)
    assert data["permissions"] == {"allow": ["Bash(ls:*)"]}
    entries = data["hooks"]["PreToolUse"]
    # User entry kept + managed entry appended.
    assert len(entries) == 2
    commands = [e["hooks"][0]["command"] for e in entries]
    assert "user/own.sh" in commands
    assert EXPECTED_REL in commands


def test_claude_respects_existing_manual_wiring(tmp_path: Path) -> None:
    """If user already wired the same hook by hand, do not add a managed dup."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / SETTINGS).write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "custom/pre_bash_shared_state_guard.sh",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    entries = _settings(tmp_path)["hooks"]["PreToolUse"]
    assert len(entries) == 1
    # Untouched
    assert entries[0]["hooks"][0]["command"] == "custom/pre_bash_shared_state_guard.sh"
    assert "_ai_hats_managed" not in entries[0]


def test_claude_updates_managed_entry_in_place(tmp_path: Path) -> None:
    """When the managed entry's command differs, update it instead of appending."""
    (tmp_path / ".claude").mkdir()
    stale = {
        "matcher": "Bash",
        "_ai_hats_managed": "ai-hats:hats-437",
        "hooks": [{"type": "command", "command": "stale/path.sh"}],
    }
    (tmp_path / SETTINGS).write_text(
        json.dumps({"hooks": {"PreToolUse": [stale]}})
    )
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    entries = _settings(tmp_path)["hooks"]["PreToolUse"]
    assert len(entries) == 1
    assert entries[0]["hooks"][0]["command"] == EXPECTED_REL


def test_gemini_provider_does_not_touch_settings(tmp_path: Path) -> None:
    GeminiProvider().ensure_runtime_hooks(tmp_path)
    assert not (tmp_path / SETTINGS).exists()


def test_malformed_json_leaves_file_untouched(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    raw = "{not valid json"
    (tmp_path / SETTINGS).write_text(raw)
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    assert (tmp_path / SETTINGS).read_text() == raw


def test_non_object_root_leaves_file_untouched(tmp_path: Path) -> None:
    """A settings file that is e.g. a list — refuse to clobber."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / SETTINGS).write_text("[]")
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    assert (tmp_path / SETTINGS).read_text() == "[]"


def test_pretool_list_user_shaped_object_left_alone(tmp_path: Path) -> None:
    """If hooks.PreToolUse is an object (not list), bail out cleanly."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / SETTINGS).write_text(
        json.dumps({"hooks": {"PreToolUse": {"unexpected": "shape"}}})
    )
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    data = _settings(tmp_path)
    assert data["hooks"]["PreToolUse"] == {"unexpected": "shape"}
