"""e2e (HATS-1992)

flow:   a HITL claude session nears its quota window, and the session's
        events.jsonl says so — from the status line, the one place a PTY
        session sees its rate limits — while the person's own bar keeps
        rendering
cmds:
    sh -c "$STATUSLINE_COMMAND"   # the string the session's settings.json
                                  # holds under statusLine, run with the
                                  # payload claude 2.1.273 was measured to send
    cat <session_dir>/events.jsonl
    cat <session_dir>/quota_warnings.json
expect: one approaching_limit line, source claude/statusline, at 82% of the
        five-hour window; nothing more on the next render of the same window,
        nothing at 17%, nothing at 82% under AI_HATS_APPROACHING_LIMIT_PERCENT=90;
        the inner bar's output on stdout and exit 0 every time — with no
        interpreter in the env too, because a bar is not a gate
why:    the notice is appended from inside a process claude spawns per render,
        not from the session process, and --settings replaces the person's
        statusLine slot — so only a real run of the settings string proves
        both that the line lands beside the session's artifacts and that the
        person's bar survives
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_APPROACHING_LIMIT_PERCENT, ENV_STATUSLINE_INNER
from ai_hats.surfaces.claude.statusline import MEMO_NAME, STATUSLINE_COMMAND
from ai_hats_observe.canonical import Notice, WorthRecording
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events

from _helpers.sessions import stand_in_session

SESSION_ID = "sid-approaching-limit"


def payload(five_hour: float, resets_at: int = 1789563600) -> str:
    """The second render's shape on 2.1.273 — rate limits present."""
    return json.dumps(
        {
            "session_id": "3bb66c38-c151-439f-9955-f1f259797c50",
            "model": {"id": "claude-opus-5[1m]", "display_name": "Opus 5 (1M context)"},
            "context_window": {"used_percentage": 3, "remaining_percentage": 97},
            "rate_limits": {
                "five_hour": {"used_percentage": five_hour, "resets_at": resets_at},
                "seven_day": {"used_percentage": 58, "resets_at": 1789714800},
            },
        }
    )


@pytest.fixture
def session(tmp_path: Path) -> SimpleNamespace:
    project = tmp_path / "project"
    project.mkdir()
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= {
        ENV_AI_HATS_PYTHON: sys.executable,
        ENV_STATUSLINE_INNER: "echo THE-PERSONS-BAR",
    }
    env.pop(ENV_APPROACHING_LIMIT_PERCENT, None)
    run_dir = project / ".agent" / "ai-hats" / "sessions" / "runs" / SESSION_ID
    return SimpleNamespace(project=project, env=env, run_dir=run_dir)


def render(session: SimpleNamespace, text: str, **env: str) -> subprocess.CompletedProcess[str]:
    """One status-line render, as claude runs it: the settings string through sh."""
    return subprocess.run(  # noqa: S603 - the production settings string, run as claude runs it
        ["sh", "-c", STATUSLINE_COMMAND],  # noqa: S607 - sh from PATH
        input=text,
        cwd=str(session.project),
        env={**session.env, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_nearing_a_quota_window_is_said_once_and_the_persons_bar_keeps_rendering(session) -> None:
    """The status-line entry HATS-1992 put into every HITL claude session's
    settings, driven as claude drives it: below the line nothing, at the line
    one notice, the same window again nothing — and the bar every time."""
    calm = render(session, payload(17))
    near = render(session, payload(82))
    again = render(session, payload(84))

    # POSITIVE CONTROL for the bar: every render printed the inner command's
    # line and exited 0 — the recorder never stood between claude and the bar
    for done in (calm, near, again):
        assert done.returncode == 0, done
        assert done.stdout.strip() == "THE-PERSONS-BAR", done.stdout
        assert "ai-hats-statusline" not in done.stderr, done.stderr

    log = session.run_dir / EVENT_LOG_JSONL
    assert log.is_file(), f"no {EVENT_LOG_JSONL} beside the session's artifacts"
    events = list(read_events(log))
    assert [type(e) for e in events] == [Notice], events
    (notice,) = events
    assert notice.reason is WorthRecording.APPROACHING_LIMIT
    assert (notice.source, notice.raw_code) == ("claude/statusline", "five_hour=82%")
    assert notice.detail == "five_hour 82% used; resets at 2026-09-16T13:00:00Z"
    assert notice.ts
    assert json.loads((session.run_dir / MEMO_NAME).read_text()) == {"five_hour": 1789563600}


def test_the_threshold_is_the_budget_in_the_sessions_env(session) -> None:
    done = render(session, payload(82), **{ENV_APPROACHING_LIMIT_PERCENT: "90"})

    assert done.returncode == 0 and done.stdout.strip() == "THE-PERSONS-BAR", done
    assert not (session.run_dir / EVENT_LOG_JSONL).exists()
    assert not (session.run_dir / MEMO_NAME).exists()


def test_without_an_interpreter_the_bar_still_renders_and_nothing_is_recorded(session) -> None:
    env = {k: v for k, v in session.env.items() if k != ENV_AI_HATS_PYTHON}
    done = subprocess.run(  # noqa: S603 - the production settings string, run as claude runs it
        ["sh", "-c", STATUSLINE_COMMAND],  # noqa: S607 - sh from PATH
        input=payload(95),
        cwd=str(session.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert done.returncode == 0, done
    assert done.stdout.strip() == "THE-PERSONS-BAR", done.stdout
    assert not (session.run_dir / EVENT_LOG_JSONL).exists()


def test_with_no_bar_of_the_persons_the_render_is_silent_and_still_records(session) -> None:
    env = {k: v for k, v in session.env.items() if k != ENV_STATUSLINE_INNER}
    done = subprocess.run(  # noqa: S603 - the production settings string, run as claude runs it
        ["sh", "-c", STATUSLINE_COMMAND],  # noqa: S607 - sh from PATH
        input=payload(95),
        cwd=str(session.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert (done.returncode, done.stdout) == (0, ""), done
    assert [e.raw_code for e in read_events(session.run_dir / EVENT_LOG_JSONL)] == ["five_hour=95%"]
