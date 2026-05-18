"""E2E baseline for `ai-hats agent <role>` — regression catcher for HATS-269."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ai_hats.cli import main



def test_agent_routes_to_subagent(project_dir: Path, mock_runners):
    res = CliRunner().invoke(
        main,
        ["agent", "session-reviewer", "--task", "do something",
         "--ticket", "HATS-1"],
    )
    assert res.exit_code == 0, res.output
    assert len(mock_runners["sub_calls"]) == 1
    call = mock_runners["sub_calls"][0]
    assert call["role_name"] == "session-reviewer"
    assert call["task"] == "do something"
    assert call["ticket_id"] == "HATS-1"


def test_agent_json_output(project_dir: Path, mock_runners):
    res = CliRunner().invoke(
        main,
        ["agent", "session-reviewer", "--task", "ping", "--json"],
    )
    assert res.exit_code == 0, res.output

    payload_line = next(
        (ln for ln in res.output.splitlines() if ln.startswith("{")),
        None,
    )
    assert payload_line is not None, f"no JSON in:\n{res.output}"
    payload = json.loads(payload_line)
    assert payload["session_id"] == "sub-1"
    assert payload["exit_code"] == 0


def test_agent_isolation_passed_through(project_dir: Path, mock_runners):
    res = CliRunner().invoke(
        main,
        ["agent", "session-reviewer", "--task", "T", "--isolation", "branch"],
    )
    assert res.exit_code == 0, res.output
    assert mock_runners["sub_calls"][0]["isolation_mode"] == "branch"
