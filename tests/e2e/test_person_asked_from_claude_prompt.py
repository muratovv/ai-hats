"""e2e (HATS-1987)

flow:   claude shows the person its own permission prompt — the one wait no
        transcript records before it resolves — and the session's events.jsonl
        says so while the person is still being asked
cmds:
    sh -c "$DISPATCHER_COMMAND"   # the string the session's settings.json
                                  # binds under Notification, run with the
                                  # payload claude 2.1.272 was measured to send
    cat <session_dir>/events.jsonl
expect: one person_asked line, kind permission, source claude/hooks — with no
        tool and no call id, because the measured payload carries neither —
        and the dispatcher's own answer is exit 0 with no decision, so the
        prompt claude shows is unchanged; a notification of another type
        leaves no line
why:    the wait is opened from inside a hook process claude spawns, not from
        the session process, so only a real run of the dispatcher string with
        the surface's real payload proves the line lands beside the session's
        other artifacts
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats_observe.canonical import AskKind, PersonAsked
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events

from _helpers.hook_chain import run_claude_dispatch
from _helpers.sessions import stand_in_session

SESSION_ID = "sid-person-asked"

# What claude 2.1.272 sends a Notification hook when it shows the permission
# prompt — measured, not the documented shape (which names the tool; this
# does not).
PERMISSION_PROMPT = {
    "session_id": "8d6f2d6d-52c2-497e-ba0c-07e2490af130",
    "prompt_id": "3a936a1f-701e-4684-b54a-8b9813fdf0db",
    "message": "Claude needs your permission",
    "notification_type": "permission_prompt",
}


@pytest.fixture
def session(tmp_path: Path) -> SimpleNamespace:
    """A stand-in claude session with a manifest that binds nothing: the
    observer needs no composed gate, only the session it runs in."""
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "hooks.json").write_text(
        json.dumps({"version": 1, "session": {"id": SESSION_ID}, "hooks": {}}),
        encoding="utf-8",
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= {
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_PYTHON": sys.executable,
    }
    log = project / ".agent" / "ai-hats" / "sessions" / "runs" / SESSION_ID / EVENT_LOG_JSONL
    return SimpleNamespace(project=project, env=env, log=log)


def test_claudes_own_permission_prompt_opens_a_wait_in_the_sessions_log(session) -> None:
    """The observer entry HATS-1987 put into every claude session's settings,
    driven as claude drives it: the prompt's notification lands as a wait, any
    other notification lands as nothing."""
    shown = run_claude_dispatch(
        session.project, session.env, event="Notification", tool="", extra=PERMISSION_PROMPT
    )
    idle = run_claude_dispatch(
        session.project,
        session.env,
        event="Notification",
        tool="",
        extra={**PERMISSION_PROMPT, "notification_type": "idle_prompt"},
    )

    # POSITIVE CONTROL: the dispatcher answered both, and changed nothing —
    # a notification hook's output is not read, and no decision is spoken.
    for done in (shown, idle):
        assert done.returncode == 0, done
        assert "permissionDecision" not in done.stdout, done.stdout
        assert "not recorded" not in done.stderr, done.stderr

    assert session.log.is_file(), f"no {EVENT_LOG_JSONL} beside the session's artifacts"
    events = list(read_events(session.log))
    assert [type(e) for e in events] == [PersonAsked], events
    (asked,) = events
    assert (asked.kind, asked.source) == (AskKind.PERMISSION, "claude/hooks")
    assert (asked.tool, asked.call_id) == (None, None), "the measured payload names neither"
    assert asked.detail == "Claude needs your permission"
    assert asked.ts
