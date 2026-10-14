"""Dispatcher tests for the kimi hook chain — kimi payloads are Claude-shaped
except ``tool_input``, which arrives as a dict-repr string (measured live)."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

from ai_hats.surfaces.kimi.claude_hook_adapter import payload_drift_notes, to_claude_hook_calls
from ai_hats.surfaces.kimi.hook_dispatcher import dispatch_hook


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)
    return path


def _manifest(cache: Path, script: Path, *, matcher: str = "Bash") -> None:
    mirrored = cache / "skills" / "guard" / "hooks" / script.name
    mirrored.parent.mkdir(parents=True)
    shutil.copy2(script, mirrored)
    (cache / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session": {
                    "id": "sid-kimi",
                    "ai_hats_dir": "/project/.agent/ai-hats",
                },
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": matcher,
                            "command": str(mirrored),
                            "tag": "ai-hats:guard:PreToolUse:test",
                        }
                    ]
                },
            }
        )
    )


def _set_session_env(monkeypatch, cache: Path) -> None:
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sid-kimi")
    monkeypatch.setenv("AI_HATS_DIR", "/project/.agent/ai-hats")
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(cache))


def _payload(command: str) -> str:
    """The envelope exactly as the spike captured it: tool_input is a STRING."""
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session_kimi-native",
            "cwd": "/project",  # ai-hats: allow-secret
            "tool_name": "Bash",
            "tool_input": repr({"command": command}),
            "tool_call_id": "tool_x",
        }
    )


def test_tool_input_repr_string_is_coerced():
    calls = to_claude_hook_calls(json.loads(_payload("echo hi")), "PreToolUse")
    assert len(calls) == 1
    payload = calls[0].payload
    assert payload["tool_name"] == "Bash"
    assert payload["tool_input"] == {"command": "echo hi"}
    assert payload["cwd"] == "/project"


def test_tool_input_dict_passes_through():
    raw = json.loads(_payload("echo hi"))
    raw["tool_input"] = {"command": "echo hi"}
    calls = to_claude_hook_calls(raw, "PreToolUse")
    assert calls[0].payload["tool_input"] == {"command": "echo hi"}


def test_tool_input_unparseable_kept_raw():
    calls = to_claude_hook_calls(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "{oops"},
        "PreToolUse",
    )
    assert calls[0].payload["tool_input"] == {"_raw": "{oops"}


def test_payload_drift_notes_silent_shapes():
    assert payload_drift_notes({"tool_name": "Bash", "tool_input": {"command": "x"}}) == []
    assert payload_drift_notes({"tool_name": "Bash", "tool_input": repr({"command": "x"})}) == []
    notes = payload_drift_notes({"tool_input": {"command": "x"}})
    assert any("tool_name" in n for n in notes)
    notes = payload_drift_notes({"tool_name": "Bash", "tool_input": "{oops"})
    assert any("tool_input" in n for n in notes)
    notes = payload_drift_notes({"tool_name": "Bash"})
    assert any("tool_input" in n for n in notes)


def test_hook_allow_exits_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(tmp_path / "allow.sh", "exit 0\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(_payload("echo hi")))
    assert code == 0


def test_hook_deny_exits_two_with_reason_on_stderr(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(
        tmp_path / "deny.sh",
        'python3 -c \'import sys; json=__import__("json"); json.load(sys.stdin); '
        'print(json.dumps({"hookSpecificOutput":{"permissionDecision":"deny",'
        '"permissionDecisionReason":"no way"}}))\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(_payload("rm -rf /")))
    assert code == 2
    assert "no way" in capsys.readouterr().err


def test_missing_manifest_refuses_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    _set_session_env(monkeypatch, cache)
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(_payload("echo hi")))
    assert code == 2
    assert "ai-hats-kimi-hook" in capsys.readouterr().err


def test_non_list_hook_rows_refuse_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    """A malformed event entry must not read as "no gates" — every other
    manifest deformation refuses closed, this one was the silent exception."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session": {"id": "sid-kimi", "ai_hats_dir": "/project/.agent/ai-hats"},
                "hooks": {"PreToolUse": {"matcher": "Bash"}},
            }
        )
    )
    _set_session_env(monkeypatch, cache)
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(_payload("echo hi")))
    assert code == 2
    assert "ai-hats-kimi-hook" in capsys.readouterr().err


def test_unsupported_event_allows(tmp_path: Path, monkeypatch, capsys) -> None:
    code = dispatch_hook("SessionStart", stdin=io.StringIO("{}"))
    assert code == 0


def test_drifted_payload_signals_on_stderr(tmp_path: Path, monkeypatch, capsys) -> None:
    """A shape change the adapter tolerates must still be SAID — a gate that
    sees no arguments allows, so the drift note is the only trace."""
    hook = _script(tmp_path / "allow.sh", "exit 0\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    drifted = json.dumps({"hook_event_name": "PreToolUse", "tool_input": "{oops"})
    code = dispatch_hook("PreToolUse", stdin=io.StringIO(drifted))
    assert code == 0
    err = capsys.readouterr().err
    assert "tool_name" in err and "tool_input" in err
