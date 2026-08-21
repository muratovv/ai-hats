from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

import ai_hats_cline.hook_dispatcher as hook_dispatcher
from ai_hats_cline.claude_hook_adapter import to_claude_hook_payloads
from ai_hats_cline.hook_dispatcher import dispatch_hook


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
                    "id": "sid-cline",
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
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sid-cline")
    monkeypatch.setenv("AI_HATS_DIR", "/project/.agent/ai-hats")
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(cache))


def test_run_commands_payload_reaches_bash_hook_and_allows(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    marker = tmp_path / "seen.json"
    hook = _script(
        tmp_path / "capture.py",
        f"python3 -c 'import json,sys; p=json.load(sys.stdin); "
        f'open({json.dumps(str(marker))}, "w").write(json.dumps(p))\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    payload = {
        "hookName": "PreToolUse",
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["echo safe"]},
        },
        "workspaceRoots": [str(tmp_path)],
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert json.loads(captured.out) == {"cancel": False}
    assert json.loads(marker.read_text()) == {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo safe"},
        "cwd": str(tmp_path),
    }


def test_pretooluse_deny_cancels_cline_tool_call(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(
        tmp_path / "deny.sh",
        "printf '%s\\n' "
        "'{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
        "\"permissionDecision\":\"deny\","
        "\"permissionDecisionReason\":\"blocked by policy\"}}'\n",
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    payload = {
        "hookName": "PreToolUse",
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["rm -rf build"]},
        },
        "workspaceRoots": [str(tmp_path)],
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert json.loads(captured.out) == {
        "cancel": True,
        "errorMessage": "blocked by policy",
    }


def test_pretooluse_ask_cancels_without_applying_updated_input(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(
        tmp_path / "ask.sh",
        "printf '%s\\n' "
        "'{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
        "\"permissionDecision\":\"ask\","
        "\"permissionDecisionReason\":\"consent required\","
        "\"updatedInput\":{\"command\":\"AI_HATS_CONSENT_ACK=1 dangerous\"}}}'\n",
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    payload = {
        "hookName": "PreToolUse",
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["dangerous"]},
        },
        "workspaceRoots": [str(tmp_path)],
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert json.loads(captured.out) == {
        "cancel": True,
        "errorMessage": (
            "consent required; Cline runtime hooks cannot ask for permission or apply "
            "hook input rewrites; grant consent outside this tool call and retry"
        ),
    }


def test_additional_context_becomes_cline_context_modification(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(
        tmp_path / "context.sh",
        "printf '%s\\n' "
        "'{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
        "\"additionalContext\":\"remember this\"}}'\n",
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    payload = {
        "hookName": "PreToolUse",
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["echo safe"]},
        },
        "workspaceRoots": [str(tmp_path)],
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert json.loads(captured.out) == {
        "cancel": False,
        "contextModification": "remember this",
    }


def test_legacy_and_current_cline_tools_use_claude_matcher_names(tmp_path: Path) -> None:
    expected = {
        "execute_command": "Bash",
        "run_commands": "Bash",
        "write_to_file": "Write",
        "replace_in_file": "Edit",
        "editor": "Edit",
        "read_file": "Read",
        "read_files": "Read",
        "search_files": "Grep",
        "search_codebase": "Grep",
        "list_files": "Glob",
    }

    for tool_name, claude_name in expected.items():
        payload = {
            "preToolUse": {
                "toolName": tool_name,
                "parameters": {"command": "echo safe"},
            },
            "workspaceRoots": [str(tmp_path)],
        }

        adapted = to_claude_hook_payloads(payload, "PreToolUse")

        assert adapted
        assert {item["tool_name"] for item in adapted} == {claude_name}


def test_apply_patch_emits_one_file_hook_payload_per_target(tmp_path: Path) -> None:
    payload = {
        "preToolUse": {
            "toolName": "apply_patch",
            "parameters": {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: src/one.py\n"
                    "@@\n-x\n+y\n"
                    "*** Add File: src/two.py\n"
                    "+z\n"
                    "*** End Patch"
                )
            },
        },
        "workspaceRoots": [str(tmp_path)],
    }

    adapted = to_claude_hook_payloads(payload, "PreToolUse")

    assert [item["tool_name"] for item in adapted] == ["MultiEdit", "MultiEdit"]
    assert [item["tool_input"]["file_path"] for item in adapted] == [
        str((tmp_path / "src" / "one.py").resolve()),
        str((tmp_path / "src" / "two.py").resolve()),
    ]


def test_missing_manifest_reports_and_fails_open(tmp_path: Path, monkeypatch, capsys) -> None:
    cache = tmp_path / "missing-cache"
    _set_session_env(monkeypatch, cache)
    payload = {
        "hookName": "PreToolUse",
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["echo safe"]},
        },
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out) == {"cancel": False}
    assert "cannot read session hook manifest" in captured.err


def test_manifest_command_escape_reports_and_fails_open(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    outside = _script(tmp_path / "outside.sh", "exit 0\n")
    cache = tmp_path / "cache"
    _manifest(cache, outside)
    manifest_path = cache / "hooks.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["hooks"]["PreToolUse"][0]["command"] = str(outside)
    manifest_path.write_text(json.dumps(manifest))
    _set_session_env(monkeypatch, cache)
    payload = {
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["echo safe"]},
        }
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out) == {"cancel": False}
    assert "escapes the session skills mirror" in captured.err


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        ("exit 7\n", "hook exited 7"),
        ("printf '%s\\n' 'not-json'\n", "hook returned invalid JSON"),
        ("printf '%s\\n' '[]'\n", "hook returned non-object JSON"),
    ],
)
def test_hook_process_failures_are_reported_and_fail_open(
    tmp_path: Path, monkeypatch, capsys, body: str, diagnostic: str
) -> None:
    hook = _script(tmp_path / "broken.sh", body)
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    payload = {
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["echo safe"]},
        }
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out) == {"cancel": False}
    assert diagnostic in captured.err


def test_hook_timeout_is_reported_and_fails_open(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(tmp_path / "slow.sh", "sleep 1\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    monkeypatch.setattr(hook_dispatcher, "HOOK_TIMEOUT_S", 0.05)
    payload = {
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["echo safe"]},
        }
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out) == {"cancel": False}
    assert "hook timed out" in captured.err


def test_unstartable_hook_is_reported_and_fails_open(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(tmp_path / "hook.sh", "exit 0\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)

    def refuse_start(*args, **kwargs):
        raise OSError("cannot spawn")

    monkeypatch.setattr(hook_dispatcher.subprocess, "Popen", refuse_start)
    payload = {
        "preToolUse": {
            "toolName": "run_commands",
            "parameters": {"commands": ["echo safe"]},
        }
    }

    code = dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out) == {"cancel": False}
    assert "hook could not start" in captured.err


def test_invalid_cline_payload_is_reported_and_fails_open(capsys) -> None:
    code = dispatch_hook("PreToolUse", stdin=io.StringIO("{broken"))
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out) == {"cancel": False}
    assert "invalid payload" in captured.err


def test_posttooluse_payload_preserves_tool_result(tmp_path: Path) -> None:
    payload = {
        "postToolUse": {
            "toolName": "editor",
            "parameters": {"path": "src/app.py"},
            "result": {"success": True},
        },
        "workspaceRoots": [str(tmp_path)],
    }

    adapted = to_claude_hook_payloads(payload, "PostToolUse")

    assert adapted == [
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"path": "src/app.py"},
            "tool_response": {"success": True},
            "cwd": str(tmp_path),
        }
    ]
