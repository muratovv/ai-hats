"""A resumed session's log holds its own run: what its record already held is an earlier run's."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

from ai_hats.runtime_common import start_event_log
from ai_hats_observe.canonical import PromptReceived, ResponseEnded, RunEnded, RunStarted
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events
from ai_hats_observe.event_log_writer import EventSource
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader
from ai_hats_observe.session import Session

SID = "77777777-8888-4999-8aaa-bbbbbbbbbbbb"


class _Surface:
    """The two calls the writer makes, over records in one directory."""

    def __init__(self, records: Path) -> None:
        self.records = records

    def event_reader(self):
        return partial(ClaudeTranscriptReader, live=True)

    def event_sources(self, root, session_id, *, provider_session_id=None):
        del root, session_id
        sources = [EventSource(self.records / f"{provider_session_id}.jsonl")]
        child = self.records / provider_session_id / "subagents" / "agent-a1.jsonl"
        return [*sources, EventSource(child, agent="a1")]


def _prompt(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "user",
        "message": {"role": "user", "content": text},
        "uuid": f"u-{text}",
        "timestamp": "2026-10-08T00:00:00.000Z",
        "sessionId": SID,
        "promptSource": "user",
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _answer(path: Path, text: str) -> None:
    record = {
        "type": "assistant",
        "message": {
            "id": f"msg-{text}",
            "role": "assistant",
            "model": "claude-x",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
        "uuid": f"a-{text}",
        "timestamp": "2026-10-08T00:00:01.000Z",
        "sessionId": SID,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _session(tmp_path: Path) -> Session:
    d = tmp_path / "session_20261008-000000-1"
    d.mkdir()
    return Session(session_id=d.name, session_dir=d)


def test_a_record_that_exists_at_start_is_read_from_its_end(tmp_path: Path) -> None:
    records = tmp_path / "records"
    main = records / f"{SID}.jsonl"
    child = records / SID / "subagents" / "agent-a1.jsonl"
    _prompt(main, "earlier run")
    _prompt(child, "earlier child")
    session = _session(tmp_path)

    writer = start_event_log(
        _Surface(records), session, cwd=tmp_path, provider_session_id=SID, main_record=True
    )
    assert writer is not None
    _prompt(main, "this run")
    _prompt(child, "this child")
    writer.close(exit_code=0)

    texts = [
        e.text
        for e in read_events(session.session_dir / EVENT_LOG_JSONL)
        if isinstance(e, PromptReceived)
    ]
    assert sorted(texts) == ["this child", "this run"], "the earlier run's lines are not repeated"


def test_an_answer_the_earlier_run_left_open_is_not_ended_in_this_one(tmp_path: Path) -> None:
    """A record killed mid-answer ends on an open response; ending it is the earlier log's."""
    records = tmp_path / "records"
    main = records / f"{SID}.jsonl"
    _prompt(main, "earlier run")
    _answer(main, "cut")
    session = _session(tmp_path)

    writer = start_event_log(
        _Surface(records), session, cwd=tmp_path, provider_session_id=SID, main_record=True
    )
    assert writer is not None
    writer.close(exit_code=0)

    events = list(read_events(session.session_dir / EVENT_LOG_JSONL))
    assert [type(e) for e in events] == [RunStarted, RunEnded]
    assert not any(isinstance(e, ResponseEnded) for e in events)
