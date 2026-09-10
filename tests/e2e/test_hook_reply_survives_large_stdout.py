"""e2e (HATS-1858)

flow:   a PostToolUse gate answers with ruff's whole output in
        `additionalContext`, which on this repo's own files runs past 11 KB
cmds:
    sh -c "$DISPATCHER_COMMAND"   # PostToolUse
expect: the advice arrives whole, and a reply that still overruns is reported
        as unreadable rather than read as a hook that said nothing
why:    the execution primitive returns a TAIL of stdout and a tail cuts a JSON
        document's head off, so a large reply did not arrive clipped, it arrived
        unparseable — and silence is an allow, so a lost verdict must never look
        like one
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _helpers.sessions import stand_in_session
from _helpers.hook_chain import run_codex_dispatch

pytestmark = pytest.mark.guards

SESSION_ID = "sid-large-reply"


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def session(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    mirror = cache / "codex-home" / "skills" / "lint" / "hooks"
    hats_dir = project / ".agent" / "ai-hats"

    def build(size: int) -> SimpleNamespace:
        doc = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "x" * size,
                }
            }
        )
        payload = mirror / "reply.json"
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_text(doc, encoding="utf-8")
        hook = _script(mirror / "lint.sh", f"#!/bin/sh\ncat >/dev/null\ncat '{payload}'\n")
        (cache / "hooks.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "session": {"id": SESSION_ID, "ai_hats_dir": str(hats_dir)},
                    "hooks": {
                        "PostToolUse": [
                            {"matcher": "Bash", "command": str(hook), "tag": "ai-hats:lint"}
                        ]
                    },
                }
            )
        )
        env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="codex")
        env |= {
            "AI_HATS_DIR": str(hats_dir),
            "AI_HATS_SESSION_CACHE_DIR": str(cache),
            "AI_HATS_PROJECT_DIR": str(project),
            "AI_HATS_PYTHON": sys.executable,
        }
        return SimpleNamespace(project=project, env=env, size=size)

    return build


def test_a_reply_far_past_the_old_tail_arrives_whole(session) -> None:
    """12 KB — above what ruff emits on this repo's own test files, and three
    times the tail the primitive used to return."""
    built = session(12_000)
    done = run_codex_dispatch(
        built.project,
        built.env,
        event="PostToolUse",
        tool="exec",
        tool_input={"command": "ruff check tests/"},
    )

    spoken = json.loads(done.stdout)["hookSpecificOutput"]
    assert len(spoken["additionalContext"]) == built.size, (
        f"the advice arrived clipped: {len(spoken['additionalContext'])} of {built.size}"
    )


def test_a_reply_that_still_overruns_is_reported_not_read_as_silence(session) -> None:
    """Silence is an allow, so a lost verdict must never look like one."""
    from ai_hats.surfaces.hook_channel import REPLY_BYTES

    built = session(REPLY_BYTES + 50_000)
    done = run_codex_dispatch(
        built.project,
        built.env,
        event="PostToolUse",
        tool="exec",
        tool_input={"command": "ruff check ."},
        timeout=60,
    )

    said = json.loads(done.stdout)
    assert said.get("decision") == "block", (
        f"an unreadable answer passed as consent:\n{done.stdout}"
    )
    assert "truncated" in said["reason"], said["reason"]
