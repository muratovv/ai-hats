"""e2e (HATS-2023)

flow:   an operator launching an unattended claude sub-agent on a machine where
        claude is not logged in
cmds:
    ai-hats agent assistant --task ping --json
expect: the run is refused before the launch attempt starts; the envelope
        carries exit_code 1 and an error naming `claude auth login`; the
        session's events.jsonl says run_started → reauthenticate → run_ended
why:    without the pre-flight probe an unauthenticated run takes a worktree,
        a cache and a role materialization, then dies on the first message with
        the reason buried in an SDK error string
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from ai_hats_observe.artifacts import (
    EVENT_LOG_JSONL,
    META_PROMPT_TXT,
    METRICS_JSON,
    ROLE_MATERIALIZATION_JSON,
    TRACE_LOG,
)
from ai_hats_observe.canonical.events import RunEnded, RunStarted
from ai_hats_observe.canonical.signals import PersonActionRequired, PersonMustAct
from ai_hats_observe.event_log import read_events

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

# What `claude auth status` prints on a machine with no credentials (2.1.278),
# exit 1 — the shape the probe refuses on.
_FAKE_CLAUDE = """#!/usr/bin/env bash
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  printf '%s\\n' '{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}'
  exit 1
fi
echo "fake claude: unexpected argv: $*" >&2
exit 97
"""


def _fake_claude_on_path(tmp_path: Path) -> str:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    binary = fake_bin / "claude"
    binary.write_text(_FAKE_CLAUDE)
    binary.chmod(0o755)
    return os.pathsep.join([str(fake_bin), os.environ.get("PATH", "")])


def _envelope(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith("{") and '"exit_code"' in line:
            return json.loads(line)
    raise AssertionError(f"no --json envelope in stdout:\n{stdout[-800:]}")


def test_e2e_agent_refuses_before_taking_a_worktree(tmp_project, tmp_path) -> None:
    result = tmp_project.run(
        "agent",
        "assistant",
        "--task",
        "ping",
        "--json",
        timeout=60.0,
        extra_env={"PATH": _fake_claude_on_path(tmp_path), "AI_HATS_NO_UPDATE_CHECK": "1"},
    )

    assert result.exit_code == 1, (
        f"exit {result.exit_code}\nstdout:\n{result.stdout[-800:]}\nstderr:\n{result.stderr[-800:]}"
    )
    assert "Traceback" not in result.stdout + result.stderr
    envelope = _envelope(result.stdout)
    assert envelope["exit_code"] == 1
    assert envelope["finalized"] is True
    assert "claude auth login" in envelope["error"]

    session_dir = Path(envelope["session_dir"])
    assert (session_dir / METRICS_JSON).exists()
    events = list(read_events(session_dir / EVENT_LOG_JSONL))
    assert [type(e) for e in events] == [RunStarted, PersonActionRequired, RunEnded]
    assert events[1].reason is PersonMustAct.REAUTHENTICATE
    assert events[1].source == "claude/readiness"
    assert events[2].ok is False

    # The launch attempt never started. A worktree or a session cache it took is
    # released at run end either way, so their absence afterwards proves
    # nothing; what the attempt writes before taking them stays. Scope: this
    # session's dir; known-present sample: the refusal line in the same trace.
    trace = (session_dir / TRACE_LOG).read_text(encoding="utf-8")
    assert "refused before launch" in trace
    for line in ("Sub-agent started", "Isolation:", "Working directory:"):
        assert line not in trace, f"the launch attempt ran: {line!r} in the trace"
    for artefact in (ROLE_MATERIALIZATION_JSON, META_PROMPT_TXT):
        assert not (session_dir / artefact).exists(), f"the launch attempt wrote {artefact}"
    startup = json.loads((session_dir / "diagnostics.json").read_text(encoding="utf-8"))["startup"]
    assert any("claude auth login" in n["text"] for n in startup["notices"])
