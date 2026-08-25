"""e2e (HATS-1391)

flow:   a developer running an agy provider session and inspecting session artifacts
cmds:
    ai-hats execute --batch -r assistant -p agy --prompt "Reply OK" --json
expect: session records audit.md with turn markers and metrics.json with token usage
        statistics
why:    without transcript resolution, session observation fails to produce audit logs
        or token telemetry
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_hats_observe.artifacts import AUDIT_MD, USAGE_JSON

from _helpers.env import checkout_pythonpath
from _helpers.project import Project

pytestmark = pytest.mark.integration

_AGY_PKG = "packages/surfaces/agy"


def _has_agy_plugin() -> bool:
    """The agy surface plugin must be importable."""
    import importlib

    try:
        importlib.import_module("ai_hats.surfaces.agy")
        return True
    except ImportError:
        return False


def test_agy_session_transcript_resolution_and_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit-integration verification: AgyProvider.resolve_transcript + AuditWriter

    Simulates an agy session producing transcript.jsonl in brain dir, verifying that
    AuditWriter produces an audit.md with 👤 turn markers and 🔧 tool calls.
    """
    from ai_hats.surfaces.agy.provider import AgyProvider
    from ai_hats_observe.audit import AuditWriter
    from ai_hats_observe.session import Session

    gemini_home = tmp_path / ".gemini"
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(gemini_home))

    session_id = "20260730-120000-1-99999"
    brain_dir = (
        gemini_home / "antigravity-cli" / "brain" / "conv-test-uuid" / ".system_generated" / "logs"
    )
    brain_dir.mkdir(parents=True)

    transcript_file = brain_dir / "transcript.jsonl"
    lines = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-07-30T15:00:00Z",
            "content": "<USER_REQUEST>\nCalculate 2+2\n</USER_REQUEST>",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-07-30T15:00:02Z",
            "thinking": "Calculation",
            "tool_calls": [
                {"name": "run_command", "args": {"CommandLine": "python3 -c 'print(2+2)'"}}
            ],
            "content": "Result is 4.",
        },
    ]
    transcript_file.write_text("\n".join(json.dumps(line) for line in lines))

    provider = AgyProvider()
    resolved = provider.resolve_transcript(tmp_path, session_id)
    assert resolved == [transcript_file]

    session_dir = tmp_path / "session_dir"
    session_dir.mkdir()
    trace_path = session_dir / "trace.log"
    trace_path.write_text("15:00:00.000 [REQ] Calculate 2+2\n15:00:02.000 [RES] ⏺ Result is 4.\n")

    session = Session(session_id=session_id, session_dir=session_dir)
    writer = AuditWriter(parser=provider.transcript_parser())
    writer.build(session, jsonl_path=resolved, keep_raw=True)

    audit_path = session_dir / AUDIT_MD
    assert audit_path.exists()
    audit_text = audit_path.read_text()
    assert "👤 Calculate 2+2" in audit_text
    assert "🔧 run_command: python3 -c 'print(2+2)'" in audit_text
    assert "👾 Result is 4." in audit_text


def test_agy_session_records_audit_and_usage(
    tmp_project: Project,
    requires_agy_auth: None,
    repo_root: Path,
) -> None:
    """Real agy session via ai-hats runner → audit.md with turn markers."""
    del requires_agy_auth

    if not _has_agy_plugin():
        pytest.skip("ai-hats-agy plugin not installed in this venv")

    checkout_env = {
        "PYTHONPATH": os.pathsep.join(
            [checkout_pythonpath(repo_root), str(repo_root / _AGY_PKG / "src")]
        )
    }

    # 1. self init configures agy provider
    tmp_project.run(
        "self",
        "init",
        "-r",
        "assistant",
        "-p",
        "agy",
        "--no-update",
        timeout=120,
        extra_env=checkout_env,
    ).expect_ok().expect_stdout_contains(
        "Provider: agy",
    )

    # 2. execute --batch runs a real headless agy session
    result = tmp_project.run(
        "execute",
        "--batch",
        "-r",
        "assistant",
        "-p",
        "agy",
        "--prompt",
        "Reply with exactly: OK. No other text.",
        "--json",
        timeout=120,
        extra_env=checkout_env,
    ).expect_ok()

    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["exit_code"] == 0, data
    session_dir = Path(data["session_dir"])

    # 3. audit.md exists with real turn markers (absent when resolve_transcript → None).
    audit_path = session_dir / AUDIT_MD
    assert audit_path.exists(), f"audit.md missing: {audit_path}"
    audit = audit_path.read_text()
    assert "- **Provider**: agy" in audit, f"audit.md missing provider marker\naudit:\n{audit}"
    assert "👤" in audit, (
        f"audit.md has no 👤 turn markers — transcript not parsed "
        f"(resolve_transcript may be missing). audit:\n{audit}"
    )

    # 4. metrics.json records duration_s > 0 and positive token usage
    metrics_path = session_dir / "metrics.json"
    assert metrics_path.exists(), f"metrics.json missing at {metrics_path}"
    metrics = json.loads(metrics_path.read_text())
    assert "duration_s" in metrics and metrics["duration_s"] > 0, (
        f"metrics missing positive duration_s: {metrics}"
    )
    assert "tokens" in metrics, f"metrics missing tokens: {metrics}"
    assert metrics["tokens"]["input"] > 0 or metrics["tokens"]["output"] > 0, (
        f"tokens zero: {metrics}"
    )

    # 5. usage.json exists without token-telemetry-unavailable flag
    usage_path = session_dir / USAGE_JSON
    if usage_path.exists():
        usage = json.loads(usage_path.read_text())
        flags = usage.get("flags", [])
        assert not any("token-telemetry-unavailable" in f for f in flags)
