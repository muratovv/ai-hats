"""Tests for AGY global hook dispatcher registration."""

from __future__ import annotations

import json
from pathlib import Path
from ai_hats.materialization import ApplyMaterializer
from ai_hats.surfaces.agy.global_hook import (
    DISPATCHER_COMMAND,
    MANAGED_DISPATCHER_TAG,
    ensure_global_dispatcher_hook,
)


def test_ensure_global_dispatcher_hook_creates_settings_when_missing(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    assert not settings_file.exists()

    changed = ensure_global_dispatcher_hook(settings_file, ApplyMaterializer())

    assert changed is True
    assert settings_file.is_file()
    data = json.loads(settings_file.read_text())
    assert "hooks" in data
    assert "PreToolUse" in data["hooks"]
    assert "PostToolUse" in data["hooks"]
    assert data["hooks"]["PreToolUse"][0]["_ai_hats_managed"] == MANAGED_DISPATCHER_TAG
    assert data["hooks"]["PreToolUse"][0]["command"] == DISPATCHER_COMMAND


def test_ensure_global_dispatcher_hook_is_idempotent(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    ensure_global_dispatcher_hook(settings_file, ApplyMaterializer())

    # Second call should return False (no changes)
    changed = ensure_global_dispatcher_hook(settings_file, ApplyMaterializer())
    assert changed is False


def test_ensure_global_dispatcher_hook_preserves_existing_user_settings(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    initial_data = {
        "model": "Gemini 3.6 Flash",
        "permissions": {"allow": ["command(*)"]},
        "hooks": {"PreToolUse": [{"matcher": "Edit", "command": "/path/to/custom_user_hook.sh"}]},
    }
    settings_file.write_text(json.dumps(initial_data, indent=2))

    changed = ensure_global_dispatcher_hook(settings_file, ApplyMaterializer())

    assert changed is True
    data = json.loads(settings_file.read_text())
    assert data["model"] == "Gemini 3.6 Flash"
    assert data["permissions"]["allow"] == ["command(*)"]
    # Custom user hook should still be present alongside managed dispatcher
    pre_hooks = data["hooks"]["PreToolUse"]
    assert len(pre_hooks) == 2
    assert any(h.get("command") == "/path/to/custom_user_hook.sh" for h in pre_hooks)
    assert any(h.get("_ai_hats_managed") == MANAGED_DISPATCHER_TAG for h in pre_hooks)
