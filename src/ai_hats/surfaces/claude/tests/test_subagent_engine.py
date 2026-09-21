"""What the SDK engine returns, and what it reports instead.

Nothing exercised ``SubagentEngine.run`` before this file: the whole suite stayed
green through the change that moved a run's cost out of the return value, which is
the same shape of blindness this card's review was about.

The SDK call is the only thing stubbed, and it is handed in rather than patched
onto the module under test (``scripts/check_test_isolation.py``, exit 1: INJECT).
The option document the engine is handed is the launch (ADR-0036 D4), so what
reaches the SDK is asserted on the launched value, not on a builder.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from types import SimpleNamespace

from ai_hats.session_artifacts import CollectedMetrics
from ai_hats.surfaces.claude.provider import ClaudeSubagentEngine, ClaudeSurface
from ai_hats.surfaces.plan import Launched


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


def _launched(**options) -> Launched:
    return Launched(args=None, sdk_options=options, env={}, prompt="")


def _run(tmp_path, metrics, *, run_blocking=None, launched=None):
    engine = ClaudeSubagentEngine(
        ClaudeSurface(), run_blocking=run_blocking or (lambda *a, **k: _sdk_result())
    )
    return engine.run(
        layout=ProjectLayout.at(tmp_path),
        work_dir=tmp_path,
        session_id="20260826-102204-1-27148",
        env={},
        model=None,
        timeout_s=60,
        metrics=metrics,
        launched=launched or _launched(cwd=str(tmp_path)),
        brief="# TASK\ndo the thing",
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


def test_the_sdk_is_handed_the_launched_document_and_the_brief(tmp_path):
    """The runner mints the surface's session id into the launched options so
    the record can be followed while the run is on; the engine hands that
    document to the SDK as it is, and the brief as the first turn."""
    handed: list = []

    def run_blocking(options, message, *, timeout_s, on_message=None):
        handed.append((options, message))
        return _sdk_result()

    _run(
        tmp_path,
        CollectedMetrics(),
        run_blocking=run_blocking,
        launched=_launched(cwd=str(tmp_path), session_id="minted-uuid", model="m"),
    )

    ((options, message),) = handed
    assert options.session_id == "minted-uuid" and options.model == "m"
    assert message == "# TASK\ndo the thing"


def test_the_return_value_carries_the_process_outcome_only(tmp_path):
    """The four accounting fields left the contract — a surface reports them, it
    does not return them, so adding a metric never edits five implementations."""
    run_result = _run(tmp_path, CollectedMetrics())

    assert (run_result.exit_code, run_result.stdout, run_result.stderr) == (0, "out", "err")
    assert run_result.timed_out is False and run_result.error is None
    for retired in ("session_id", "total_cost_usd", "num_turns", "stop_reason"):
        assert not hasattr(run_result, retired), f"{retired} is back on SurfaceRunResult"
