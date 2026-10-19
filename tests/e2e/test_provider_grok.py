"""e2e (HATS-2076)

flow:   a developer lists providers, then a headless grok session runs against
        a stand-in binary that writes the on-disk ledger
cmds:
    ai-hats list providers
    grok --output-format streaming-messages-json --always-approve -p <prompt>
expect: grok is listed beside claude; the fake session's updates.jsonl resolves
        and GrokParser reads tokens from the sibling usage.json, not from a
        second copy of the same turn
why:    a surface reaches the binary only through the ai_hats.providers group,
        and a grok session with no parser is blind. The fake binary keeps the
        gate off the live API.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
import tomllib
import uuid
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath

from ai_hats.surfaces.grok import GrokSurface
from ai_hats.surfaces.grok.parser import GrokParser
from ai_hats.surfaces.grok.provider import GrokSubagentEngine

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

# HATS-2076: the pin lives outside the catalog docstring, which the catalog strips.
_SURFACE = "grok"
_DECLARED = "ai_hats.surfaces.grok.provider:GrokSurface"

_FAKE = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    from pathlib import Path

    args = sys.argv[1:]
    session = "missing"
    if "--session-id" in args:
        session = args[args.index("--session-id") + 1]
    home = Path(os.environ["GROK_HOME"])
    folder = home / "sessions" / "cwd" / session
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "updates.jsonl").write_text(
        json.dumps({
            "timestamp": 10,
            "params": {"update": {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "text", "text": "say pong"},
            }},
        }) + "\\n" +
        json.dumps({
            "timestamp": 11,
            "params": {"update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "pong"},
            }},
        }) + "\\n" +
        json.dumps({
            "timestamp": 12,
            "params": {"update": {
                "sessionUpdate": "turn_completed",
                "usage": {
                    "inputTokens": 200,
                    "cachedReadTokens": 80,
                    "cacheCreationTokens": 0,
                    "outputTokens": 8,
                    "modelCalls": 1,
                    "primaryModelId": "grok-test",
                },
            }},
        }) + "\\n"
    )
    (folder / "usage.json").write_text(json.dumps({
        "sessionId": session,
        "session": {
            "inputTokens": 100,
            "cachedReadTokens": 40,
            "cacheCreationTokens": 0,
            "outputTokens": 4,
            "modelCalls": 1,
            "primaryModelId": "grok-test",
            "modelUsage": {
                "grok-test": {
                    "inputTokens": 100,
                    "cachedReadTokens": 40,
                    "cacheCreationTokens": 0,
                    "outputTokens": 4,
                    "modelCalls": 1,
                }
            },
        },
    }))
    print(json.dumps({
        "type": "result",
        "result": "pong",
        "session_id": session,
        "num_turns": 1,
        "stop_reason": "end_turn",
    }))
    """
)


def _declared_entry_point(repo_root: Path) -> str | None:
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    return pyproject["project"]["entry-points"]["ai_hats.providers"].get(_SURFACE)


def test_grok_surface_is_discovered_by_the_binary(ai_hats_shim: Path, repo_root: Path):
    assert _declared_entry_point(repo_root) == _DECLARED, (
        f"ai-hats no longer declares {_SURFACE!r} under ai_hats.providers"
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)

    result = subprocess.run(
        [str(ai_hats_shim), "list", "providers"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert _SURFACE in result.stdout, result.stdout
    assert "claude" in result.stdout, result.stdout


def test_fake_grok_binary_session_resolves_and_parses(tmp_path: Path, monkeypatch):
    home = tmp_path / "grok-home"
    home.mkdir()
    monkeypatch.setenv("GROK_HOME", str(home))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    binary = bin_dir / "grok"
    binary.write_text(_FAKE)
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))

    session = str(uuid.uuid4())
    surface = GrokSurface()
    cmd = surface.get_run_command(surface.get_cli_launch_args(["grok"], session, False), "say pong")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert proc.returncode == 0, proc.stderr
    assert "pong" in proc.stdout

    found = surface.resolve_transcript(tmp_path, "sid", provider_session_id=session)
    assert len(found) == 1
    parsed = GrokParser().parse(found[0], tmp_path / "missing.trace")
    assert parsed.turns[0].response == "pong"
    # usage.json says 100−40, not the turn_completed's 200−80.
    assert parsed.agg_usage["input_tokens"] == 60
    assert parsed.agg_usage["output_tokens"] == 4
    assert parsed.model_stats["grok-test"]["calls"] == 1

    # The engine path records the id the stream reported.
    class _Metrics:
        def __init__(self) -> None:
            self.values: dict = {}

        def record(self, values) -> None:
            self.values.update(values)

    metrics = _Metrics()
    result = GrokSubagentEngine().run(
        layout=None,  # type: ignore[arg-type]
        work_dir=tmp_path,
        session_id="sid",
        env={"GROK_HOME": str(home), "PATH": os.environ["PATH"]},
        model=None,
        timeout_s=30,
        metrics=metrics,
        launched=type("L", (), {"args": tuple(cmd)})(),
        brief=None,
        provider_session_id=session,
    )
    assert result.exit_code == 0
    assert result.stdout == "pong"
    assert metrics.values["provider_session_id"] == session
