"""e2e (HATS-1967, HATS-1987)

flow:   an operator's session has a gate that refuses one path and asks about
        another; while the session runs, its own events.jsonl says which gate
        refused which call, and when the run is waiting on the person
cmds:
    sh -c "$DISPATCHER_COMMAND"   # the string a settings.json entry holds,
                                  # run once for a refused call, once for an
                                  # allowed one, once for one the gate asks about
    cat <session_dir>/events.jsonl
expect: one gate_verdict line per call, appended by the hook process into the
        session's own log — deny naming the guard and the tool, then allow,
        then ask followed by a person_asked under the same call id — and the
        verdict claude received is unchanged by the recording
why:    the verdict is recorded from inside the hook process, not the session
        process, so an in-process test of dispatch() cannot see whether a real
        hook run finds its session and lands the line beside the session's
        other artifacts — the one fact no transcript carries
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats_observe.canonical import AskKind, GateDecision, GatePoint, GateVerdict, PersonAsked
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events

from _helpers.hook_chain import run_claude_dispatch
from _helpers.sessions import stand_in_session

pytestmark = [pytest.mark.integration, pytest.mark.guards, pytest.mark.observe]

SESSION_ID = "sid-gate-verdict"
OFF_LIMITS = "/etc/passwd"
ASK_FIRST = "/etc/hosts"


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def session(tmp_path: Path) -> SimpleNamespace:
    """A stand-in claude session whose only PreToolUse gate refuses one path
    and asks the person about another."""
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    guard = _script(
        cache / "plugin" / "skills" / "guards" / "guard.sh",
        "#!/bin/sh\n"
        + "payload=$(cat)\n"
        + f'if printf \'%s\' "$payload" | grep -q "{OFF_LIMITS}"; then\n'
        + "  printf '%s' '"
        + json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{OFF_LIMITS} is off limits",
                }
            }
        )
        + "'\n"
        + f'elif printf \'%s\' "$payload" | grep -q "{ASK_FIRST}"; then\n'
        + "  printf '%s' '"
        + json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": f"{ASK_FIRST} needs a person",
                }
            }
        )
        + "'\nfi\n",
    )
    rows = [{"command": str(guard), "matcher": "Bash", "tag": "ai-hats:guard"}]
    (cache / "hooks.json").write_text(
        json.dumps({"version": 1, "session": {"id": SESSION_ID}, "hooks": {"PreToolUse": rows}}),
        encoding="utf-8",
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= {
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_PYTHON": sys.executable,
    }
    env.pop("AI_HATS_GATE_BROKEN_ACK", None)
    log = project / ".agent" / "ai-hats" / "sessions" / "runs" / SESSION_ID / EVENT_LOG_JSONL
    return SimpleNamespace(project=project, env=env, log=log)


def _spoken(done) -> dict:
    assert done.stdout.strip(), f"the dispatcher said nothing at all: {done.stderr!r}"
    return json.loads(done.stdout)["hookSpecificOutput"]


def test_each_verdict_lands_in_the_sessions_event_log(session) -> None:
    """Refused, allowed, asked: three hook processes, four lines, in order — the
    recording HATS-1967 put into every dispatcher, seen from outside it, and
    the wait on the person HATS-1987 opens beside an ``ask`` verdict."""
    refused = run_claude_dispatch(
        session.project, session.env, tool="Bash", tool_input={"command": f"cat {OFF_LIMITS}"}
    )
    allowed = run_claude_dispatch(
        session.project, session.env, tool="Bash", tool_input={"command": "echo hello"}
    )
    asked = run_claude_dispatch(
        session.project,
        session.env,
        tool="Bash",
        tool_input={"command": f"cat {ASK_FIRST}"},
        tool_use_id="toolu-ask-1",
    )

    # POSITIVE CONTROL: the chain really refused, allowed, then asked — and the
    # answer claude got is what the record must agree with. A bare allow is
    # silence on this surface, so the second run is judged by its status.
    assert _spoken(refused)["permissionDecision"] == "deny", refused.stdout
    assert allowed.returncode == 0 and "permissionDecision" not in allowed.stdout, allowed
    assert _spoken(asked)["permissionDecision"] == "ask", asked.stdout
    assert "gate verdict not recorded" not in refused.stderr + allowed.stderr + asked.stderr

    assert session.log.is_file(), f"no {EVENT_LOG_JSONL} beside the session's artifacts"
    events = list(read_events(session.log))
    assert [type(e) for e in events] == [GateVerdict, GateVerdict, GateVerdict, PersonAsked], events
    deny, allow, ask, wait = events
    assert (deny.point, deny.decision) == (GatePoint.BEFORE_TOOL, GateDecision.DENY)
    assert "guard" in deny.hook, deny
    assert OFF_LIMITS in deny.reason
    assert (deny.tool, deny.source) == ("Bash", "chain")
    assert (allow.decision, allow.hook) == (GateDecision.ALLOW, "")
    assert deny.ts and allow.ts and deny.ts <= allow.ts
    assert (ask.decision, ask.call_id) == (GateDecision.ASK, "toolu-ask-1"), ask
    assert (wait.kind, wait.call_id, wait.tool, wait.source) == (
        AskKind.PERMISSION,
        "toolu-ask-1",
        "Bash",
        "chain",
    )
    assert ASK_FIRST in (wait.detail or "")
