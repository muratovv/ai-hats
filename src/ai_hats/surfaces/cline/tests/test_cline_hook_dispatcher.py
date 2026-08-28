from __future__ import annotations

import io
import json
import re
import shutil
from pathlib import Path

import pytest

from ai_hats.surfaces.cline.claude_hook_adapter import to_claude_hook_payloads
from ai_hats.surfaces.cline.hook_dispatcher import dispatch_hook
from ai_hats.surfaces.hook_channel import HOOK_TIMEOUT_ENV


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


def _append_manifest_hook(cache: Path, script: Path, *, skill_name: str) -> None:
    mirrored = cache / "skills" / skill_name / "hooks" / script.name
    mirrored.parent.mkdir(parents=True)
    shutil.copy2(script, mirrored)
    manifest_path = cache / "hooks.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["hooks"]["PreToolUse"].append(
        {
            "matcher": "Bash",
            "command": str(mirrored),
            "tag": f"ai-hats:{skill_name}:PreToolUse:test",
        }
    )
    manifest_path.write_text(json.dumps(manifest))


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
        '\'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"deny",'
        '"permissionDecisionReason":"blocked by policy"}}\'\n',
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


def test_later_allow_cannot_override_earlier_deny(tmp_path: Path, monkeypatch, capsys) -> None:
    deny = _script(
        tmp_path / "deny.sh",
        "printf '%s\\n' "
        '\'{"hookSpecificOutput":{"permissionDecision":"deny",'
        '"permissionDecisionReason":"first refusal"}}\'\n',
    )
    allow = _script(
        tmp_path / "allow.sh",
        'printf \'%s\\n\' \'{"hookSpecificOutput":{"permissionDecision":"allow"}}\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, deny)
    _append_manifest_hook(cache, allow, skill_name="later-allow")
    _set_session_env(monkeypatch, cache)
    payload = {
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
        "errorMessage": "first refusal",
    }


def test_pretooluse_ask_cancels_without_applying_updated_input(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(
        tmp_path / "ask.sh",
        "printf '%s\\n' "
        '\'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"ask",'
        '"permissionDecisionReason":"consent required",'
        '"updatedInput":{"command":"AI_HATS_CONSENT_ACK=1 dangerous"}}}\'\n',
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
            "consent required; this surface cannot carry the consent this gate "
            "asked for, so grant it outside this tool call and retry"
        ),
    }


def test_additional_context_becomes_cline_context_modification(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(
        tmp_path / "context.sh",
        "printf '%s\\n' "
        '\'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"additionalContext":"remember this"}}\'\n',
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
        "bash": "Bash",
        "execute_command": "Bash",
        "run_commands": "Bash",
        "write_to_file": "Write",
        "replace_in_file": "Edit",
        "editor": "Edit",
        "read_file": "Read",
        "read_files": "Read",
        "search_files": "Grep",
        "search_codebase": "Grep",
        "search": "Grep",
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


_CALL = {"preToolUse": {"toolName": "run_commands", "parameters": {"commands": ["echo safe"]}}}


def _refused(capsys) -> dict:
    """The reply, asserted to be a refusal that names a way past it.

    Every case below used to answer ``{"cancel": false}`` with a line on stderr.
    HATS-1339 chose that on purpose; HATS-1439 is what it cost — seven gates
    vanished mid-session and the session ran on with them off.
    """
    captured = capsys.readouterr()
    reply = json.loads(captured.out)
    assert reply["cancel"] is True, "a gate ai-hats could not deliver let the call through"
    assert re.search(r"AI_HATS_[A-Z0-9_]+", reply["errorMessage"]), (
        f"the refusal names no way past it: {reply['errorMessage']!r}"
    )
    return {"reply": reply, "err": captured.err}


def test_a_missing_manifest_refuses_instead_of_passing_the_call(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _set_session_env(monkeypatch, tmp_path / "missing-cache")

    assert dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(_CALL))) == 0
    out = _refused(capsys)
    assert "cannot read session hook manifest" in out["reply"]["errorMessage"]


def test_a_command_outside_the_mirror_refuses(tmp_path: Path, monkeypatch, capsys) -> None:
    outside = _script(tmp_path / "outside.sh", "exit 0\n")
    cache = tmp_path / "cache"
    _manifest(cache, outside)
    manifest_path = cache / "hooks.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["hooks"]["PreToolUse"][0]["command"] = str(outside)
    manifest_path.write_text(json.dumps(manifest))
    _set_session_env(monkeypatch, cache)

    assert dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(_CALL))) == 0
    out = _refused(capsys)
    assert "escapes the session skills mirror" in out["reply"]["errorMessage"]


@pytest.mark.parametrize(
    "body",
    ["exit 7\n", "printf '%s\\n' 'not-json'\n", "printf '%s\\n' '[]'\n"],
)
def test_a_hook_that_answers_unreadably_refuses(
    tmp_path: Path, monkeypatch, capsys, body: str
) -> None:
    hook = _script(tmp_path / "broken.sh", body)
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)

    assert dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(_CALL))) == 0
    _refused(capsys)


def test_a_hook_that_overruns_its_budget_refuses(tmp_path: Path, monkeypatch, capsys) -> None:
    hook = _script(tmp_path / "slow.sh", "sleep 5\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    monkeypatch.setenv(HOOK_TIMEOUT_ENV, "0.3")

    assert dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(_CALL))) == 0
    out = _refused(capsys)
    assert HOOK_TIMEOUT_ENV in out["reply"]["errorMessage"], "the budget names no way to widen it"


def test_a_hook_that_cannot_be_executed_refuses(tmp_path: Path, monkeypatch, capsys) -> None:
    """Dropping the +x bit must not be a way to disarm a gate quietly."""
    hook = _script(tmp_path / "hook.sh", "exit 0\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    _set_session_env(monkeypatch, cache)
    Path(json.loads((cache / "hooks.json").read_text())["hooks"]["PreToolUse"][0]["command"]).chmod(
        0o644
    )

    assert dispatch_hook("PreToolUse", stdin=io.StringIO(json.dumps(_CALL))) == 0
    _refused(capsys)


def test_an_unreadable_payload_refuses(capsys) -> None:
    assert dispatch_hook("PreToolUse", stdin=io.StringIO("{broken")) == 0
    out = _refused(capsys)
    assert "invalid payload" in out["reply"]["errorMessage"]


def test_an_event_nothing_can_bind_to_still_passes(capsys) -> None:
    """The control: not every pass-through is a defect. Nothing composed can
    bind to an unknown event, so no gate was missed."""
    assert dispatch_hook("SessionStart", stdin=io.StringIO("{}")) == 0
    assert json.loads(capsys.readouterr().out) == {"cancel": False}


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


def test_a_terminal_call_with_no_command_still_reaches_its_gates() -> None:
    """An empty spread used to return no payload at all, and a payload-less call
    runs the dispatcher's loop zero times — a Bash gate skipped on a Bash call."""
    from ..claude_hook_adapter import to_claude_hook_payloads

    payloads = to_claude_hook_payloads(
        {"preToolUse": {"toolName": "bash", "parameters": {"commands": [None]}}},
        "PreToolUse",
    )
    assert len(payloads) == 1
    assert payloads[0]["tool_name"] == "Bash"


def test_a_terminal_call_still_spreads_over_every_command() -> None:
    """The control for the case above: a real spread is untouched."""
    from ..claude_hook_adapter import to_claude_hook_payloads

    payloads = to_claude_hook_payloads(
        {"preToolUse": {"toolName": "bash", "parameters": {"commands": ["a", "b"]}}},
        "PreToolUse",
    )
    assert [p["tool_input"]["command"] for p in payloads] == ["a", "b"]
