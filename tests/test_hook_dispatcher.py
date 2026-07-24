"""Tests for ai-hats-hook-dispatcher CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.cli.hook_dispatcher import dispatch_hook


def test_dispatcher_noop_when_session_id_missing(monkeypatch) -> None:
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)

    res = dispatch_hook("PreToolUse")
    assert res == 0


def test_dispatcher_noop_when_hooks_json_missing(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sid-test")
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(project))

    res = dispatch_hook("PreToolUse")
    assert res == 0


def test_dispatcher_executes_matching_session_hook(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()

    cache_dir = project / ".agent" / "ai-hats" / ".cache" / "sessions" / "sid-exec"
    cache_dir.mkdir(parents=True)

    marker_file = tmp_path / "hook_ran.txt"
    hook_script = tmp_path / "test_hook.sh"
    hook_script.write_text(f"#!/bin/sh\necho 'OK' > '{marker_file}'\n")
    hook_script.chmod(0o755)

    hooks_manifest = {
        "PreToolUse": [
            {
                "matcher": "Edit",
                "command": str(hook_script),
                "tag": "test:hook",
            }
        ]
    }
    (cache_dir / "hooks.json").write_text(json.dumps(hooks_manifest))

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sid-exec")
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(project))

    res = dispatch_hook("PreToolUse", tool_name="Edit")
    assert res == 0
    assert marker_file.is_file()
    assert marker_file.read_text().strip() == "OK"
