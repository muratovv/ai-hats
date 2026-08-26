"""What the SDK engine returns, and what it reports instead (HATS-1826).

Nothing exercised ``SubagentEngine.run`` before this file: the whole suite stayed
green through the change that moved a run's cost out of the return value, which is
the same shape of blindness this card's review was about.

The SDK call is the only thing stubbed, and it is handed in rather than patched
onto the module under test (``scripts/check_test_isolation.py``, exit 1: INJECT).
Everything up to it — artifact reuse, option assembly, the first user message —
runs for real, so a break there is this test's failure too.
"""

from __future__ import annotations

from types import SimpleNamespace

from ai_hats.session_artifacts import BuiltArtifacts, CollectedMetrics
from ai_hats.surfaces.claude.provider import ClaudeSubagentEngine, ClaudeSurface


def _sdk_result() -> SimpleNamespace:
    return SimpleNamespace(
        exit_code=0,
        stdout="out",
        stderr="err",
        timed_out=False,
        error=None,
        claude_session_id="7f7f4497-uuid",
        total_cost_usd=0.0286019,
        num_turns=5,
        stop_reason="end_turn",
    )


def _run(tmp_path, metrics):
    engine = ClaudeSubagentEngine(ClaudeSurface(), run_blocking=lambda *a, **k: _sdk_result())
    return engine.run(
        result=SimpleNamespace(
            name="r", priorities=[], merged_injection="", rules=[], skills=[], checks=()
        ),
        project_dir=tmp_path,
        work_dir=tmp_path,
        session_id="20260826-102204-1-27148",
        task="do the thing",
        ticket_id="",
        env={},
        model=None,
        timeout_s=60,
        metrics=metrics,
        artifacts=BuiltArtifacts(),
    )


def test_the_run_reports_its_cost_into_the_sink(tmp_path):
    metrics = CollectedMetrics()

    _run(tmp_path, metrics)

    assert metrics.values == {
        "claude_session_id": "7f7f4497-uuid",
        "total_cost_usd": 0.0286019,
        "num_turns": 5,
        "stop_reason": "end_turn",
    }


def test_the_return_value_carries_the_process_outcome_only(tmp_path):
    """The four accounting fields left the contract — a surface reports them, it
    does not return them, so adding a metric never edits five implementations."""
    run_result = _run(tmp_path, CollectedMetrics())

    assert (run_result.exit_code, run_result.stdout, run_result.stderr) == (0, "out", "err")
    assert run_result.timed_out is False and run_result.error is None
    for retired in ("session_id", "total_cost_usd", "num_turns", "stop_reason"):
        assert not hasattr(run_result, retired), f"{retired} is back on SurfaceRunResult"
