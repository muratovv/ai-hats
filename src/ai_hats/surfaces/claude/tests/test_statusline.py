"""The status line as the HITL session's quota producer.

The status-line payload is the one place a PTY session sees its rate-limit
windows (the SDK stream has ``RateLimitEvent``; the transcript has nothing).
It arrives on every render, so the reading is a threshold said once per window
per reset — the payload shapes here are the ones measured on Claude Code 2.1.273.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from ai_hats.env import ENV_APPROACHING_LIMIT_PERCENT
from ai_hats.session_identity import SessionIdentity
from ai_hats.surfaces.claude.statusline import MEMO_NAME, SOURCE, main, quota_notices
from ai_hats_observe.canonical import Notice, Timestamp, WorthRecording
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events

TS = Timestamp("2026-09-16T09:30:00.000Z")

# The second render of a session: rate limits present, both windows below 80.
CALM = {
    "session_id": "3bb66c38-c151-439f-9955-f1f259797c50",
    "model": {"id": "claude-opus-5[1m]", "display_name": "Opus 5 (1M context)"},
    "context_window": {"used_percentage": 3, "remaining_percentage": 97},
    "rate_limits": {
        "five_hour": {"used_percentage": 17, "resets_at": 1789563600},
        "seven_day": {"used_percentage": 57.99999999999999, "resets_at": 1789714800},
    },
}

# The first render: before any API response there is no `rate_limits` at all.
FIRST = {k: v for k, v in CALM.items() if k != "rate_limits"}


def near(five_hour: float, seven_day: float = 58, resets_at: int = 1789563600) -> dict:
    payload = json.loads(json.dumps(CALM))
    payload["rate_limits"]["five_hour"] = {"used_percentage": five_hour, "resets_at": resets_at}
    payload["rate_limits"]["seven_day"]["used_percentage"] = seven_day
    return payload


def test_a_window_past_the_threshold_is_said_once_per_reset() -> None:
    events, warned = quota_notices(near(82), {}, threshold=80, ts=TS)

    (notice,) = events
    assert isinstance(notice, Notice)
    assert notice.reason is WorthRecording.APPROACHING_LIMIT
    assert notice.raw_code == "five_hour=82%"
    assert notice.detail == "five_hour 82% used; resets at 2026-09-16T13:00:00Z"
    assert notice.source == SOURCE == "claude/statusline"
    assert notice.ts == TS
    assert warned == {"five_hour": 1789563600}

    # the next render of the same window says nothing more
    again, still = quota_notices(near(83), warned, threshold=80, ts=TS)
    assert again == []
    assert still == warned

    # a new reset is a new window — said again
    later, moved = quota_notices(near(81, resets_at=1789581600), warned, threshold=80, ts=TS)
    assert [n.raw_code for n in later] == ["five_hour=81%"]
    assert moved == {"five_hour": 1789581600}


def test_below_the_threshold_nothing_is_said() -> None:
    assert quota_notices(CALM, {}, threshold=80, ts=TS) == ([], {})


def test_a_first_render_with_no_rate_limits_says_nothing() -> None:
    assert quota_notices(FIRST, {}, threshold=80, ts=TS) == ([], {})


def test_every_window_is_judged_and_a_float_at_the_line_counts() -> None:
    events, warned = quota_notices(near(80.0, seven_day=99.5), {}, threshold=80, ts=TS)
    assert [n.raw_code for n in events] == ["five_hour=80%", "seven_day=100%"]
    assert warned == {"five_hour": 1789563600, "seven_day": 1789714800}

    # 79.999 is not 80: the threshold is a line, not a rounding
    assert quota_notices(near(79.999, seven_day=10), {}, threshold=80, ts=TS)[0] == []


def test_a_threshold_above_one_hundred_never_fires() -> None:
    assert quota_notices(near(100, seven_day=100), {}, threshold=101, ts=TS)[0] == []


def test_a_window_with_no_reset_is_still_said_once() -> None:
    payload = near(90)
    del payload["rate_limits"]["five_hour"]["resets_at"]
    events, warned = quota_notices(payload, {}, threshold=80, ts=TS)
    assert [n.detail for n in events] == ["five_hour 90% used"]
    assert warned == {"five_hour": None}
    assert quota_notices(payload, warned, threshold=80, ts=TS)[0] == []


def test_a_malformed_window_is_skipped_not_raised() -> None:
    payload = near(90)
    payload["rate_limits"]["seven_day"] = "?"
    payload["rate_limits"]["spend_limit"] = {"used_percentage": "high"}
    events, warned = quota_notices(payload, {}, threshold=80, ts=TS)
    assert [n.raw_code for n in events] == ["five_hour=90%"]
    assert warned == {"five_hour": 1789563600}


# --- the entry point ---------------------------------------------------------


@pytest.fixture
def session(tmp_path: Path) -> tuple[dict[str, str], Path]:
    session_dir = tmp_path / "runs" / "session_x"
    session_dir.mkdir(parents=True)
    identity = SessionIdentity(
        id="sid-statusline",
        role="assistant",
        provider="claude",
        project_dir=tmp_path,
        session_dir=session_dir,
    )
    return identity.to_env(), session_dir


def _run(env: dict[str, str], payload: dict) -> int:
    return main(stdin=io.StringIO(json.dumps(payload)), environ=env)


def test_main_records_the_notice_beside_the_sessions_events_and_remembers(session) -> None:
    env, session_dir = session

    assert _run(env, near(82)) == 0
    assert _run(env, near(84)) == 0

    events = list(read_events(session_dir / EVENT_LOG_JSONL))
    assert [type(e) for e in events] == [Notice], events
    assert events[0].raw_code == "five_hour=82%"
    assert events[0].ts
    assert json.loads((session_dir / MEMO_NAME).read_text()) == {"five_hour": 1789563600}


def test_main_reads_the_threshold_from_the_budget(session) -> None:
    env, session_dir = session

    assert _run({**env, ENV_APPROACHING_LIMIT_PERCENT: "90"}, near(82)) == 0
    assert not (session_dir / EVENT_LOG_JSONL).exists()
    assert not (session_dir / MEMO_NAME).exists()

    # POSITIVE CONTROL: the same render fires at the default
    assert _run(env, near(82)) == 0
    assert (session_dir / EVENT_LOG_JSONL).exists()


def test_main_outside_a_session_says_so_and_exits_clean(capsys) -> None:
    assert main(stdin=io.StringIO(json.dumps(near(95))), environ={}) == 0
    assert "no session" in capsys.readouterr().err


def test_main_never_raises_on_an_unreadable_payload(session, capsys) -> None:
    env, session_dir = session
    assert main(stdin=io.StringIO("not json"), environ=env) == 0
    assert "payload" in capsys.readouterr().err
    assert not (session_dir / EVENT_LOG_JSONL).exists()
