"""The OpenCode hook path, which had no test of any kind before this.

Every case here is one the plugin used to get wrong by construction: it read a
verdict off an exit code and understood two shapes, so a gate could refuse a
call in the dialect every shipped hook speaks and be waved through.
"""

from __future__ import annotations


import io
import json
from pathlib import Path

import pytest

from ai_hats.env import ENV_SESSION_CACHE_DIR
from ai_hats_observe.trace import ENV_SESSION_ID

from ..hook_dispatcher import dispatch_hook
from ..profile import PROFILE

_SESSION = "sid-opencode"


def _mirror(cache: Path) -> Path:
    """Where this surface's session skills actually land.

    Spelled through the profile rather than restated, so a test cannot keep
    passing against a mirror root the dispatcher no longer uses.
    """
    root = PROFILE.skills_root(cache)
    assert root is not None, "opencode declares a mirror under the cache"
    return root


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _emit(**hook_specific) -> str:
    spoken = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **hook_specific}})
    return f"cat >/dev/null\nprintf '%s' {json.dumps(spoken)}\n"


def _manifest(cache: Path, *rows, session: str = _SESSION) -> None:
    path = cache / "opencode" / "hooks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "session": {"id": session},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": matcher,
                            "command": str(script),
                            "tag": f"ai-hats:{script.name}",
                        }
                        for script, matcher in rows
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def _judge(
    cache: Path, *, tool: str = "bash", args: dict | None = None, event="PreToolUse"
) -> dict:
    request = json.dumps(
        {
            "event": event,
            "payload": {"tool_name": tool, "tool_input": args or {"command": "echo hi"}},
        }
    )
    out = io.StringIO()
    environ = {ENV_SESSION_ID: _SESSION, ENV_SESSION_CACHE_DIR: str(cache)}
    import contextlib

    with contextlib.redirect_stdout(out):
        code = dispatch_hook(stdin=io.StringIO(request), environ=environ)
    assert code == 0
    return json.loads(out.getvalue())


def test_a_denial_in_the_dialect_every_hook_speaks_is_honoured(tmp_path: Path) -> None:
    """THE defect: `permissionDecision` appeared nowhere in the plugin, so this
    refusal — the shape all eight shipped hooks emit — let the call through."""
    hook = _script(
        _mirror(tmp_path) / "deny.sh",
        _emit(permissionDecision="deny", permissionDecisionReason="off limits"),
    )
    _manifest(tmp_path, (hook, "Bash"))
    verdict = _judge(tmp_path)
    assert verdict["decision"] == "deny"
    assert "off limits" in verdict["reason"]


def test_a_quiet_chain_allows(tmp_path: Path) -> None:
    """The control: the refusal above is not simply everything being denied."""
    hook = _script(_mirror(tmp_path) / "quiet.sh", "cat >/dev/null\n")
    _manifest(tmp_path, (hook, "Bash"))
    assert _judge(tmp_path)["decision"] == "allow"


def test_a_later_hook_overrides_an_earlier_one(tmp_path: Path) -> None:
    """A single-hook test cannot see this, and this is how a green test came to
    coexist with the opposite live behaviour."""
    allowed = _script(_mirror(tmp_path) / "allow.sh", _emit(permissionDecision="allow"))
    denied = _script(
        _mirror(tmp_path) / "deny.sh",
        _emit(permissionDecision="deny", permissionDecisionReason="B says no"),
    )
    _manifest(tmp_path, (allowed, "Bash"), (denied, "Bash"))
    verdict = _judge(tmp_path)
    assert (verdict["decision"], verdict["reason"]) == ("deny", "B says no")


def test_a_file_edit_by_patch_reaches_the_edit_gates(tmp_path: Path) -> None:
    """`patch` was absent from the plugin's nine-entry table, and an unmapped
    tool returned before the matcher loop ever ran."""
    hook = _script(
        _mirror(tmp_path) / "guard.sh",
        _emit(permissionDecision="deny", permissionDecisionReason="not that file"),
    )
    _manifest(tmp_path, (hook, "Edit|Write|MultiEdit"))
    verdict = _judge(tmp_path, tool="patch", args={"file_path": "/x"})
    assert verdict["decision"] == "deny"


def test_a_nudge_reaches_the_reader_with_its_author(tmp_path: Path) -> None:
    hook = _script(_mirror(tmp_path) / "hint.sh", _emit(additionalContext="prefer Grep"))
    _manifest(tmp_path, (hook, "Bash"))
    verdict = _judge(tmp_path)
    assert verdict["nudges"] == [{"text": "prefer Grep", "hook": "ai-hats:hint.sh"}]


def test_a_manifest_from_another_session_refuses(tmp_path: Path) -> None:
    hook = _script(_mirror(tmp_path) / "quiet.sh", "cat >/dev/null\n")
    _manifest(tmp_path, (hook, "Bash"), session="someone-else")
    verdict = _judge(tmp_path)
    assert verdict["decision"] == "deny"
    assert verdict["hatch_env"]


def test_a_missing_manifest_refuses_and_names_a_way_past(tmp_path: Path) -> None:
    verdict = _judge(tmp_path)
    assert verdict["decision"] == "deny"
    assert verdict["hatch_env"] == "AI_HATS_GATE_BROKEN_ACK"


def test_a_gate_whose_script_vanished_refuses(tmp_path: Path) -> None:
    hook = _script(_mirror(tmp_path) / "gone.sh", "cat >/dev/null\n")
    _manifest(tmp_path, (hook, "Bash"))
    hook.unlink()  # safe-delete: ok this test's own tmp_path fixture
    assert _judge(tmp_path)["decision"] == "deny"


@pytest.mark.parametrize("event", ["SessionStart", "PreCompact", ""])
def test_an_event_nothing_can_bind_to_allows(tmp_path: Path, event: str) -> None:
    """The other control: not every pass is a defect — nothing composed binds
    to these, so no gate was skipped."""
    assert _judge(tmp_path, event=event)["decision"] == "allow"


class TestTheManifestCommandIsChecked:
    """codex and cline both refuse a command outside the session mirror; this
    surface executed whatever string the manifest named.

    The profile has carried the mirror root the whole time — ``skills_subpath``
    with a ``skills_root()`` accessor — and nothing called it, so the field read
    as documentation while the check it exists for was absent here.
    """

    def test_a_command_outside_the_session_mirror_refuses(self, tmp_path: Path) -> None:
        outside = _script(tmp_path / "elsewhere" / "evil.sh", "cat >/dev/null\n")
        _manifest(tmp_path, (outside, "Bash"))
        verdict = _judge(tmp_path)
        assert verdict["decision"] == "deny"
        assert "escapes the session skills mirror" in verdict["reason"]

    def test_a_command_that_is_not_executable_refuses(self, tmp_path: Path) -> None:
        inert = _script(_mirror(tmp_path) / "inert.sh", "cat >/dev/null\n")
        inert.chmod(0o644)
        _manifest(tmp_path, (inert, "Bash"))
        verdict = _judge(tmp_path)
        assert verdict["decision"] == "deny"
        assert "not an executable session file" in verdict["reason"]

    def test_a_command_inside_the_mirror_still_runs(self, tmp_path: Path) -> None:
        """The positive control: the check must not refuse everything."""
        good = _script(_mirror(tmp_path) / "ok.sh", _emit(permissionDecision="allow"))
        _manifest(tmp_path, (good, "Bash"))
        assert _judge(tmp_path)["decision"] == "allow"


def test_the_file_argument_reaches_the_hook_under_the_name_it_defends(tmp_path: Path) -> None:
    """opencode names it `filePath`; every shipped file gate reads `file_path`
    or `path` and ALLOWS when neither is there (`wt_gate.py`).

    So the mutation gates matched the tool and then had nothing to inspect. The
    name is not a guess: opencode's own session database records `filePath` on
    every read, edit and write it has ever made on this machine, the way codex's
    `exec` came out of its rollout logs.
    """
    seen = tmp_path / "seen.json"
    hook = _script(_mirror(tmp_path) / "record.sh", f"cat > {seen}\n")
    _manifest(tmp_path, (hook, "Edit|Write|MultiEdit"))

    _judge(tmp_path, tool="edit", args={"filePath": "/etc/passwd", "oldString": "a"})

    handed = json.loads(seen.read_text())["tool_input"]
    assert handed["file_path"] == "/etc/passwd", f"the gate was handed nothing to inspect: {handed}"


def test_a_key_the_surface_already_spells_correctly_is_left_alone(tmp_path: Path) -> None:
    """`grep` and `glob` send `path`, which the gates read directly."""
    seen = tmp_path / "seen.json"
    hook = _script(_mirror(tmp_path) / "record.sh", f"cat > {seen}\n")
    _manifest(tmp_path, (hook, "Grep"))

    _judge(tmp_path, tool="grep", args={"path": "/etc", "pattern": "root"})

    assert json.loads(seen.read_text())["tool_input"]["path"] == "/etc"
