"""e2e (HATS-1966)

flow:   a developer runs a sub-agent session and then reads the session's own
        machine-readable record of what happened in it
cmds:
    ai-hats agent assistant --task "Reply with just: ok" --model claude-haiku-4-5
    cat <session_dir>/events.jsonl
expect: the session dir holds events.jsonl beside the audit.md / usage.json it
        already wrote, every line stamped events/v1 and decoding to a canonical
        event, with the assistant's call reported once and carrying its usage
why:    the artifact is written by a pipeline step inside the session process, so
        an in-process test of the step says nothing about what a finished session
        leaves on disk — and a session that quietly stops writing it looks exactly
        like a session that had nothing to record
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json

import pytest

from ai_hats_observe.artifacts import AUDIT_MD, USAGE_JSON
from ai_hats_observe.canonical.events import ResponseEnded, ResponseStarted
from ai_hats_observe.event_log import EVENT_LOG_JSONL, EVENT_SCHEMA_VERSION, read_events

from _helpers.project import Project
from _helpers.sessions import snapshot_session_dirs, wait_for_new_session_dir

pytestmark = [pytest.mark.integration, pytest.mark.observe, pytest.mark.surfaces]


# Probe shape copied from ``test_agent_orchestration.py``: haiku and a one-token
# reply, because HATS-1966 is about the artifact a finished session leaves, not
# about what the model said.
DRIVE_MODEL = "claude-haiku-4-5"
TASK_PROMPT = "Reply with just: ok"
AGENT_TIMEOUT = 90.0


def test_a_finished_session_leaves_its_canonical_event_log(
    tmp_project: Project,
    requires_claude_auth,  # noqa: ARG001 — skip-marker fixture
) -> None:
    """``ai-hats agent assistant`` → events.jsonl in the session directory."""
    snapshot = snapshot_session_dirs(tmp_project.path)

    tmp_project.run(
        "agent",
        "assistant",
        "--task",
        TASK_PROMPT,
        "--model",
        DRIVE_MODEL,
        timeout=AGENT_TIMEOUT,
    ).expect_ok()

    session_dir = wait_for_new_session_dir(snapshot, role="assistant", timeout=30.0)

    # POSITIVE CONTROL: the two artifacts this one joins, written off the same
    # transcript — their presence means the session ran and its transcript
    # resolved, so an absent events.jsonl below is the new step failing.
    assert (session_dir / AUDIT_MD).is_file(), f"no audit.md in {session_dir}"
    assert (session_dir / USAGE_JSON).is_file(), f"no usage.json in {session_dir}"

    event_log = session_dir / EVENT_LOG_JSONL
    assert event_log.is_file(), (
        f"no {EVENT_LOG_JSONL} in {session_dir} — the finalize pipeline wrote the "
        f"other artifacts but not this one; contents: {sorted(p.name for p in session_dir.iterdir())}"
    )

    lines = event_log.read_text(encoding="utf-8").splitlines()
    assert lines, "events.jsonl is empty — a real session emitted no events"
    for line in lines:
        assert json.loads(line)["v"] == EVENT_SCHEMA_VERSION, f"unversioned line: {line[:120]}"

    events = list(read_events(event_log))
    assert len(events) == len(lines), "a line written by this session did not decode back"

    started = [e for e in events if isinstance(e, ResponseStarted)]
    ended = [e for e in events if isinstance(e, ResponseEnded)]
    assert started and len(ended) == len(started), (
        f"{len(started)} responses started, {len(ended)} ended — the reader lost a call"
    )
    assert any(e.usage.output_tokens > 0 for e in ended), (
        f"no response reported output tokens: {[e.usage for e in ended]}"
    )
