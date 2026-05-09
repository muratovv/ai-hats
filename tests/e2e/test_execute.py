"""E2E baseline for `ai-hats execute` — regression catcher for HATS-269."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ai_hats.cli import main



def test_execute_interactive_with_prompt(project_dir: Path, mock_runners):
    # Use a real prompt file so _resolve_prompt accepts it
    pf = project_dir / "myprompt.txt"
    pf.write_text("ping")
    res = CliRunner().invoke(
        main, ["execute", "--role", "judge", "--prompt", str(pf)],
    )
    assert res.exit_code == 0, res.output
    assert len(mock_runners["wrap_calls"]) == 1
    call = mock_runners["wrap_calls"][0]
    assert call["role_override"] == "judge"
    # prompt prepended as first positional in extra_args
    assert call["extra_args"][0] == "ping"


def test_execute_interactive_no_prompt(project_dir: Path, mock_runners):
    res = CliRunner().invoke(main, ["execute", "--role", "judge"])
    assert res.exit_code == 0, res.output
    call = mock_runners["wrap_calls"][0]
    assert call["extra_args"] == []


def test_execute_batch_routes_to_subagent(project_dir: Path, mock_runners):
    pf = project_dir / "myprompt.txt"
    pf.write_text("ping")
    res = CliRunner().invoke(
        main,
        ["execute", "--role", "session-reviewer", "--batch",
         "--prompt", str(pf), "--ticket", "HATS-1"],
    )
    assert res.exit_code == 0, res.output
    assert len(mock_runners["sub_calls"]) == 1
    call = mock_runners["sub_calls"][0]
    assert call["role_name"] == "session-reviewer"
    assert call["task"] == "ping"
    assert call["ticket_id"] == "HATS-1"


def test_execute_batch_json_output(project_dir: Path, mock_runners):
    pf = project_dir / "myprompt.txt"
    pf.write_text("ping")
    res = CliRunner().invoke(
        main,
        ["execute", "--role", "session-reviewer", "--batch",
         "--prompt", str(pf), "--json"],
    )
    assert res.exit_code == 0, res.output
    # JSON line in stdout
    payload_line = next(
        (ln for ln in res.output.splitlines() if ln.startswith("{")),
        None,
    )
    assert payload_line is not None, f"no JSON in:\n{res.output}"
    payload = json.loads(payload_line)
    assert payload["session_id"] == "sub-1"
    assert payload["exit_code"] == 0


def test_execute_unknown_short_prompt_fails_fast(project_dir: Path, mock_runners):
    res = CliRunner().invoke(
        main,
        ["execute", "--role", "judge", "--batch", "--prompt", "no-such-name"],
    )
    assert res.exit_code != 0
    assert "no such" in res.output.lower() or "not a known" in res.output.lower()
    # Pipeline / runner not reached
    assert mock_runners["sub_calls"] == []
    assert mock_runners["wrap_calls"] == []
