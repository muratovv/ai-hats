"""The status line as the HITL session's quota producer.

The status-line payload is the one place a PTY session sees its rate-limit
windows (the SDK stream has ``RateLimitEvent``; the transcript has nothing).
It arrives on every render, so the reading is a threshold said once per window
per reset — the payload shapes here are the ones measured on Claude Code 2.1.273.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest

from ai_hats.env import ENV_APPROACHING_LIMIT_PERCENT
from ai_hats.session_identity import SessionIdentity
from ai_hats.surfaces.claude.channel import ClaudeChannel
from ai_hats.surfaces.claude.statusline import (
    MEMO_NAME,
    SOURCE,
    is_render,
    person_settings_files,
    person_status_line,
    quota_notices,
    record_quota,
)
from ai_hats.surfaces.hook_dispatch import dispatch
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


def near(five_hour: float, seven_day: float = 58, resets_at: int | None = 1789563600) -> dict:
    payload = json.loads(json.dumps(CALM))
    payload["rate_limits"]["five_hour"] = {"used_percentage": five_hour, "resets_at": resets_at}
    payload["rate_limits"]["seven_day"]["used_percentage"] = seven_day
    return payload


def said(payload: dict, warned: dict, threshold: float = 80) -> list[Notice]:
    return [notice for _, _, notice in quota_notices(payload, warned, threshold=threshold, ts=TS)]


# --- the pure reading ------------------------------------------------------------


def test_a_window_past_the_threshold_is_said_with_its_reset() -> None:
    ((window, reset, notice),) = quota_notices(near(82), {}, threshold=80, ts=TS)

    assert (window, reset) == ("five_hour", 1789563600)
    assert isinstance(notice, Notice)
    assert notice.reason is WorthRecording.APPROACHING_LIMIT
    assert notice.raw_code == "five_hour=82%"
    assert notice.detail == "five_hour 82% used; resets at 2026-09-16T13:00:00Z"
    assert notice.source == SOURCE == "claude/statusline"
    assert notice.ts == TS

    # the same window, already remembered for this reset, says nothing more
    assert said(near(83), {"five_hour": 1789563600}) == []
    # a new reset is a new window — said again
    assert [
        n.raw_code for n in said(near(81, resets_at=1789581600), {"five_hour": 1789563600})
    ] == ["five_hour=81%"]


def test_below_the_threshold_nothing_is_said() -> None:
    assert said(CALM, {}) == []


def test_a_first_render_with_no_rate_limits_says_nothing() -> None:
    assert said(FIRST, {}) == []


def test_every_window_is_judged_and_a_float_at_the_line_counts() -> None:
    assert [n.raw_code for n in said(near(80.0, seven_day=99.5), {})] == [
        "five_hour=80%",
        "seven_day=100%",
    ]
    # 79.999 is not 80: the threshold is a line, not a rounding
    assert said(near(79.999, seven_day=10), {}) == []


def test_a_threshold_above_one_hundred_never_fires() -> None:
    assert said(near(100, seven_day=100), {}, threshold=101) == []


def test_a_window_with_no_reset_is_still_said_once() -> None:
    payload = near(90, resets_at=None)
    ((window, reset, notice),) = quota_notices(payload, {}, threshold=80, ts=TS)
    assert (window, reset, notice.detail) == ("five_hour", None, "five_hour 90% used")
    assert said(payload, {"five_hour": None}) == []


def test_a_malformed_window_is_skipped_not_raised() -> None:
    payload = near(90)
    payload["rate_limits"]["seven_day"] = "?"
    payload["rate_limits"]["spend_limit"] = {"used_percentage": "high"}
    assert [n.raw_code for n in said(payload, {})] == ["five_hour=90%"]


@pytest.mark.parametrize(
    ("used", "resets_at", "expect"),
    [
        (float("nan"), 1789563600, []),
        (float("inf"), 1789563600, []),
        (True, 1789563600, []),
        (82, float("inf"), ["five_hour 82% used"]),
        (82, 1789563600000, ["five_hour 82% used; resets at 1789563600000"]),
        (82, True, ["five_hour 82% used"]),
    ],
)
def test_a_number_that_is_not_one_never_raises(used, resets_at, expect) -> None:
    """``json.loads`` accepts NaN and Infinity; a bool is an int; a millisecond
    epoch is no second of any calendar. None of them may take a render down."""
    payload = near(used, seven_day=10, resets_at=resets_at)
    assert [n.detail for n in said(payload, {})] == expect


def test_a_render_is_told_by_what_it_carries() -> None:
    assert is_render(CALM)
    assert not is_render(FIRST), "before the first response there is nothing to read"
    assert is_render({**CALM, "hook_event_name": "Status"}), "a name for it would not hide it"


# --- one render, recorded and remembered ---------------------------------------


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


def _memo(session_dir: Path) -> dict:
    return json.loads((session_dir / MEMO_NAME).read_text())


def test_record_quota_says_every_window_once_and_remembers_what_it_recorded(session) -> None:
    env, session_dir = session

    first = record_quota(near(90, seven_day=95), env)
    again = record_quota(near(91, seven_day=96), env)

    assert [n.raw_code for n in first] == ["five_hour=90%", "seven_day=95%"]
    assert again == []
    assert [e.raw_code for e in read_events(session_dir / EVENT_LOG_JSONL)] == [
        "five_hour=90%",
        "seven_day=95%",
    ]
    assert _memo(session_dir) == {"five_hour": 1789563600, "seven_day": 1789714800}


def test_the_memo_follows_the_record_never_precedes_it(session, capsys) -> None:
    """A notice that could not land must not be remembered as said, or the
    window stays silent until its reset. The fault is real: the log's path is
    taken by a directory, so the append fails the way a full disk would."""
    env, session_dir = session
    (session_dir / EVENT_LOG_JSONL).mkdir()

    assert record_quota(near(90), env) == []
    assert "not recorded" in capsys.readouterr().err
    assert not (session_dir / MEMO_NAME).exists()

    # POSITIVE CONTROL: with the record landing, the same render is remembered
    (session_dir / EVENT_LOG_JSONL).rmdir()  # safe-delete: ok empty-dir
    assert [n.raw_code for n in record_quota(near(90), env)] == ["five_hour=90%"]
    assert _memo(session_dir) == {"five_hour": 1789563600}


def test_record_quota_reads_the_threshold_from_the_budget(session) -> None:
    env, session_dir = session

    assert record_quota(near(82), {**env, ENV_APPROACHING_LIMIT_PERCENT: "90"}) == []
    assert record_quota(near(82), {**env, ENV_APPROACHING_LIMIT_PERCENT: "82.5"}) == []
    assert not (session_dir / MEMO_NAME).exists()
    # POSITIVE CONTROL: a decimal threshold parses, and the default fires
    assert [
        n.raw_code for n in record_quota(near(82), {**env, ENV_APPROACHING_LIMIT_PERCENT: "81.5"})
    ]
    assert record_quota(near(82), env) == [], "already said under the lower threshold"


def test_record_quota_outside_a_session_says_so_and_records_nothing(capsys) -> None:
    assert record_quota(near(95), {}) == []
    assert "no session" in capsys.readouterr().err


def test_concurrent_renders_say_a_window_once(session) -> None:
    """The resident dispatcher answers renders from threads of one process."""
    env, session_dir = session
    results: list[list[Notice]] = []
    threads = [
        threading.Thread(target=lambda: results.append(record_quota(near(90), env)))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(len(r) for r in results) == 1
    assert [e.raw_code for e in read_events(session_dir / EVENT_LOG_JSONL)] == ["five_hour=90%"]
    assert _memo(session_dir) == {"five_hour": 1789563600}


def test_a_render_through_the_dispatcher_lands_beside_the_sessions_events(session, capsys) -> None:
    """The route is the hook dispatcher's: a payload naming no event is observed,
    the notice recorded, and the answer to the surface is nothing at all."""
    env, session_dir = session
    stdin = io.StringIO(json.dumps(near(82)))

    status = dispatch(ClaudeChannel(), stdin=stdin, environ=env, hook_environ=env)

    assert status == 0
    assert capsys.readouterr().out == ""
    events = list(read_events(session_dir / EVENT_LOG_JSONL))
    assert [type(e) for e in events] == [Notice], events
    assert (events[0].source, events[0].raw_code) == (SOURCE, "five_hour=82%")
    assert events[0].ts


# --- the person's own bar ------------------------------------------------------


def _settings(path: Path, document: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document if isinstance(document, str) else json.dumps(document))
    return path


def test_the_settings_chain_is_the_users_home_then_the_project(tmp_path: Path) -> None:
    """The user's file by the rule ``tool_home`` applies — ``CLAUDE_CONFIG_DIR``
    over ``HOME/.claude`` — read from the env handed in, not the process's."""
    project = tmp_path / "proj"
    by_home = person_settings_files({"HOME": "/h"}, project)
    by_config = person_settings_files({"HOME": "/h", "CLAUDE_CONFIG_DIR": "/c"}, project)
    by_nothing = person_settings_files({}, project)

    assert by_home == [
        Path("/h/.claude/settings.json"),
        project / ".claude" / "settings.json",
        project / ".claude" / "settings.local.json",
    ]
    assert by_config[0] == Path("/c/settings.json")
    assert by_nothing == by_home[1:]


def test_the_persons_status_line_is_read_the_way_claude_layers_settings(tmp_path: Path) -> None:
    """User, then project, then local — the last file naming a command wins, and
    a file naming none leaves the one before it standing."""
    user = _settings(
        tmp_path / "home" / ".claude" / "settings.json",
        {"statusLine": {"type": "command", "command": "bash ~/bar.sh", "padding": 0}},
    )
    project = _settings(tmp_path / "proj" / ".claude" / "settings.json", {"permissions": {}})
    local = _settings(
        tmp_path / "proj" / ".claude" / "settings.local.json",
        {"statusLine": {"type": "command", "command": "~/other.sh"}},
    )
    missing = tmp_path / "nowhere.json"

    assert person_status_line([user]) == {
        "type": "command",
        "command": "bash ~/bar.sh",
        "padding": 0,
    }
    assert person_status_line([user, project]) == person_status_line([user])
    assert person_status_line([user, project, local]) == {
        "type": "command",
        "command": "~/other.sh",
    }
    assert person_status_line([missing, project]) is None
    assert person_status_line([]) is None


def test_a_settings_file_that_will_not_parse_is_skipped_aloud(tmp_path: Path, capsys) -> None:
    broken = _settings(tmp_path / "broken.json", "{not json")
    user = _settings(tmp_path / "ok.json", {"statusLine": {"command": "echo hi"}})
    # a statusLine without a command string is not a bar
    empty = _settings(tmp_path / "empty.json", {"statusLine": {"type": "command", "command": ""}})

    assert person_status_line([broken, user, empty]) == {"command": "echo hi"}
    assert str(broken) in capsys.readouterr().err
