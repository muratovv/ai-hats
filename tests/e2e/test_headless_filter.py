"""e2e (HATS-2020)

flow:   a script runs a role's session from a file of turns and keeps what
        happened in a file of events, with no terminal anywhere
cmds:
    ai-hats headless -p claude -r assistant < turns.ndjson > events.ndjson
expect: exit 0; line 1 of events.ndjson is the headless/v1 header; every line
        after it is byte-for-byte the session's events.jsonl, run_started to
        run_ended, one turn_ended per turn; the session is finalized with its
        audit, and two turns stay below the retro threshold; a surface that
        dies mid-turn with 3 makes it exit 3, still recorded
why:    this is the whole contract of the filter — stdin in, the log out, the
        exit code as the outcome — so a script needs nothing but files and $?
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

HEADER_KEYS = {
    "v": str,
    "session_id": str,
    "session_dir": str,
    "log": str,
    "holder_pid": int,
    "provider_session_id": str,
    "started_at": str,
    "events": str,
    "commands": list,
}


def _prompt(text: str) -> str:
    return json.dumps({"v": "commands/v1", "cmd": "prompt", "text": text}) + "\n"


def _filter(project, tmp_path: Path, turns: str, *args: str) -> tuple[int, bytes, str]:
    """Run the filter the way a script would: a file in, a file out, $? back."""
    env = session_env(install(tmp_path), project)
    (tmp_path / "turns.ndjson").write_text(turns)
    with (
        (tmp_path / "turns.ndjson").open("rb") as stdin,
        (tmp_path / "events.ndjson").open("wb") as stdout,
    ):
        done = subprocess.run(
            [str(project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant", *args],
            cwd=project.path,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    return done.returncode, (tmp_path / "events.ndjson").read_bytes(), done.stderr


def test_e2e_a_file_of_turns_becomes_a_file_of_events(tmp_project, tmp_path) -> None:
    code, out, err = _filter(tmp_project, tmp_path, _prompt("first") + _prompt("@recall"))

    assert code == 0, f"exit {code}\nstderr:\n{err[-2000:]}"
    header_line, _, events = out.partition(b"\n")
    header = json.loads(header_line)
    assert header["v"] == "headless/v1"
    assert {k: type(v) for k, v in header.items()} == HEADER_KEYS, header

    log = Path(header["log"])
    assert events == log.read_bytes(), "stdout after line 1 must be the log, byte for byte"
    kinds = [json.loads(line)["event"] for line in events.splitlines()]
    assert kinds[0] == "run_started" and kinds[-1] == "run_ended"
    # Every turn ends after its own answer. Sent ahead, the next turn's prompt
    # may land before that end: the binary keeps the queue (ADR-0038 D3).
    ends = [i for i, k in enumerate(kinds) if k == "turn_ended"]
    assert len(ends) == 2
    for n, at in enumerate(ends, start=1):
        assert kinds[:at].count("response_ended") >= n, kinds

    session_dir = Path(header["session_dir"])
    assert json.loads((session_dir / "metrics.json").read_text())["finalized"] is True
    assert (session_dir / "audit.md").exists()
    retro = (session_dir / "retro.log").read_text()
    assert "\tdecision\tskip: below threshold (turns=2<" in retro, retro
    assert "\toutcome\t" not in retro, retro


def test_e2e_a_surface_that_dies_mid_turn_is_the_exit_code(tmp_project, tmp_path) -> None:
    code, out, err = _filter(tmp_project, tmp_path, _prompt("first") + _prompt("@die 3"))

    assert code == 3, f"exit {code}\nstderr:\n{err[-2000:]}"
    events = [json.loads(line) for line in out.splitlines()[1:]]
    assert events[-1]["event"] == "run_ended"
    assert (events[-1]["ok"], events[-1]["raw_code"]) == (False, "3")
    session_dir = Path(json.loads(out.splitlines()[0])["session_dir"])
    assert json.loads((session_dir / "metrics.json").read_text())["finalized"] is True
