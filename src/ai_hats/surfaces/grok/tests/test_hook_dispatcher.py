"""Dispatcher tests — grok speaks Claude's document and exits 0, not 2."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

from ai_hats.surfaces.grok.claude_hook_adapter import payload_drift_notes, to_claude_hook_calls
from ai_hats.surfaces.grok.hook_dispatcher import dispatch_hook


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)
    return path


def _manifest(
    cache: Path, script: Path, *, event: str = "PreToolUse", matcher: str = "Bash"
) -> None:
    mirrored = cache / "skills" / "guard" / "hooks" / script.name
    mirrored.parent.mkdir(parents=True)
    shutil.copy2(script, mirrored)
    (cache / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session": {"id": "sid-grok", "ai_hats_dir": "/project/.agent/ai-hats"},
                "hooks": {
                    event: [
                        {
                            "matcher": matcher,
                            "command": str(mirrored),
                            "tag": f"ai-hats:guard:{event}:test",
                        }
                    ]
                },
            }
        )
    )


def _env(monkeypatch, cache: Path) -> None:
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sid-grok")
    monkeypatch.setenv("AI_HATS_DIR", "/project/.agent/ai-hats")
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(cache))


def _payload(**extra) -> str:
    body = {
        "hook_event_name": "PreToolUse",
        "toolName": "run_terminal_command",
        "toolInput": {"command": "echo hi"},
        "cwd": "work",
    }
    body.update(extra)
    return json.dumps(body)


def _said(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_native_name_is_spoken_as_the_matcher_vocabulary():
    calls = to_claude_hook_calls(json.loads(_payload()), "PreToolUse")
    assert calls[0].payload["tool_name"] == "Bash"
    assert calls[0].native_tool == "run_terminal_command"
    assert calls[0].payload["tool_input"] == {"command": "echo hi"}


def test_snake_case_alias_is_accepted():
    raw = {
        "tool_name": "write",
        "tool_input": {"file_path": "a"},
        "tool_response": "ok",
    }
    calls = to_claude_hook_calls(raw, "PostToolUse")
    assert calls[0].payload["tool_name"] == "Write"
    assert calls[0].payload["tool_response"] == "ok"


def test_payload_drift_notes_name_a_missing_tool():
    assert payload_drift_notes(json.loads(_payload())) == []
    notes = payload_drift_notes({"cwd": "work"})
    assert any("tool_name" in note for note in notes)
    assert any("tool_input" in note for note in notes)


def test_deny_is_json_and_exits_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(
        tmp_path / "deny.sh",
        "python3 -c 'import json,sys; json.load(sys.stdin); "
        'print(json.dumps({"hookSpecificOutput":{"permissionDecision":"deny",'
        '"permissionDecisionReason":"no way"}}))\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _env(monkeypatch, cache)
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(_payload()))
    assert code == 0
    spoken = _said(capsys)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "no way" in spoken["permissionDecisionReason"]


def test_a_ticketed_ask_is_refused_without_the_rewrite(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(
        tmp_path / "ask.sh",
        "python3 -c 'import json,sys; json.load(sys.stdin); "
        'print(json.dumps({"hookSpecificOutput":{"permissionDecision":"ask",'
        '"permissionDecisionReason":"consent",'
        '"updatedInput":{"command":"TICKET=1 echo hi"}}}))\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _env(monkeypatch, cache)
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(_payload()))
    assert code == 0
    spoken = _said(capsys)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "updatedInput" not in spoken
    assert "cannot carry the consent" in spoken["permissionDecisionReason"]


def test_an_ask_without_a_rewrite_is_still_asked(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(
        tmp_path / "ask.sh",
        "python3 -c 'import json,sys; json.load(sys.stdin); "
        'print(json.dumps({"hookSpecificOutput":{"permissionDecision":"ask",'
        '"permissionDecisionReason":"confirm this push"}}))\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _env(monkeypatch, cache)
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(_payload()))
    assert code == 0
    spoken = _said(capsys)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "ask"
    assert "confirm this push" in spoken["permissionDecisionReason"]
    assert "updatedInput" not in spoken


def test_post_tool_use_deny_becomes_a_nudge(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(
        tmp_path / "late.sh",
        "python3 -c 'import json,sys; json.load(sys.stdin); "
        'print(json.dumps({"hookSpecificOutput":{"permissionDecision":"deny",'
        '"permissionDecisionReason":"too late"}}))\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook, event="PostToolUse", matcher="Bash")
    _env(monkeypatch, cache)
    code = dispatch_hook("PostToolUse", stdin=io.StringIO(_payload()))
    assert code == 0
    spoken = _said(capsys)["hookSpecificOutput"]
    assert "permissionDecision" not in spoken
    assert "too late" in spoken["additionalContext"]


def test_missing_manifest_denies_in_the_document(tmp_path: Path, monkeypatch, capsys) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    _env(monkeypatch, cache)
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(_payload()))
    assert code == 0
    spoken = _said(capsys)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "ai-hats-grok-hook" in spoken["permissionDecisionReason"]


def test_unsupported_event_allows(capsys) -> None:
    assert dispatch_hook("SessionStart", stdin=io.StringIO("{}")) == 0
    assert capsys.readouterr().out == ""
