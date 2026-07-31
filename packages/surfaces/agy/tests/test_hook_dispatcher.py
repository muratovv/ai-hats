"""Tests for ai-hats-hook-dispatcher in AGY surface plugin."""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats_agy.hook_dispatcher import dispatch_hook


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


def _seed_manifest(cache_dir: Path, hook_script: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "PreToolUse": [{"matcher": "Edit", "command": str(hook_script), "tag": "test:hook"}]
    }
    (cache_dir / "hooks.json").write_text(json.dumps(manifest))


def _marker_hook(tmp_path: Path) -> tuple[Path, Path]:
    marker_file = tmp_path / "hook_ran.txt"
    hook_script = tmp_path / "test_hook.sh"
    hook_script.write_text(f"#!/bin/sh\necho 'OK' > '{marker_file}'\n")
    hook_script.chmod(0o755)
    return marker_file, hook_script


def test_dispatcher_executes_hook_from_pinned_cache_dir(tmp_path: Path, monkeypatch) -> None:
    """The pin is the channel — the manifest lives wherever it points (HATS-1398)."""
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "out-of-tree" / "sessions" / "sid-exec"
    marker_file, hook_script = _marker_hook(tmp_path)
    _seed_manifest(cache_dir, hook_script)

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sid-exec")
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(project))
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(cache_dir))

    res = dispatch_hook("PreToolUse", tool_name="Edit")
    assert res == 0
    assert marker_file.read_text().strip() == "OK"


def test_dispatcher_falls_back_to_legacy_in_tree_path_out_loud(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A session built before the move still fires — and says so (HATS-1373 class)."""
    project = tmp_path / "project"
    legacy = project / ".agent" / "ai-hats" / ".cache" / "sessions" / "sid-legacy"
    marker_file, hook_script = _marker_hook(tmp_path)
    _seed_manifest(legacy, hook_script)

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sid-legacy")
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(project))
    monkeypatch.delenv("AI_HATS_SESSION_CACHE_DIR", raising=False)

    res = dispatch_hook("PreToolUse", tool_name="Edit")
    assert res == 0
    assert marker_file.read_text().strip() == "OK"
    assert "AI_HATS_SESSION_CACHE_DIR unset" in capsys.readouterr().err
