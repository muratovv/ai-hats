"""Pipeline-level integration test for ``finalize-subagent`` (HATS-530).

Closes the acceptance criterion that a SubAgent finalize pipeline run
triggers the auto-retro reviewer when retro threshold is met. We drive
the pipeline directly via :func:`ai_hats.pipeline.pipeline.run` rather
than via a real ``ai-hats execute --batch`` subprocess — the latter is
HATS-498's territory (real CLI, real claude binary) and costs API
tokens to spawn the reviewer's own session. This test stays free by
monkey-patching the spawn.

What this test verifies (vs. the unit tests for ``MaybeSpawnSessionReviewer``):
the YAML wiring — `finalize-subagent.yaml` now includes the new step
in the position that makes it fire on every SubAgent terminal path.

The pipeline is loaded from the **worktree's** YAML via an explicit
file path (NOT ``load_core_pipeline`` which resolves to the installed
package — would mask test failures during local edits before reinstall).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ai_hats.paths import runs_dir
from ai_hats.pipeline.loader import load_pipeline
from ai_hats.pipeline.pipeline import run as run_pipeline


# Locate the worktree's library/ — the test file lives in <wt>/tests/.
WORKTREE_ROOT = Path(__file__).resolve().parent.parent
FINALIZE_SUBAGENT_YAML = (
    WORKTREE_ROOT / "library" / "core" / "pipelines" / "finalize-subagent.yaml"
)


def _seed_project_with_retro_policy(
    tmp_path: Path,
    *,
    min_turns: int,
    min_tool_calls: int,
    metrics: dict,
) -> Path:
    """Write ai-hats.yaml + metrics.json under runs_dir; return session_dir."""
    (tmp_path / "ai-hats.yaml").write_text(yaml.dump({
        "schema_version": 2,
        "provider": "claude",
        "active_role": "primary",
        "feedback": {
            "session_retro": {
                "policy": "smart",
                "smart_threshold": {
                    "min_turns": min_turns,
                    "min_tool_calls": min_tool_calls,
                },
                "mode": "programmatic",
                "background": True,
            },
        },
    }))
    session_dir = runs_dir(tmp_path) / "session_test"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metrics.json").write_text(json.dumps(metrics))
    return session_dir


def test_pipeline_wires_make_audit_then_spawn(tmp_path, monkeypatch):
    """Step IDs in `finalize-subagent` are exactly
    [make_audit, compute_usage, maybe_spawn_session_reviewer].

    Pins the wiring at the YAML level — any future drift (e.g. someone
    re-adding `run_session_end` without considering SubAgent contract,
    or dropping `compute_usage`) surfaces here. HATS-664 inserted
    `compute_usage` right after `make_audit` (shared JSONL source).
    """
    pipe = load_pipeline(FINALIZE_SUBAGENT_YAML)
    step_names = [s.io.name for s in pipe.steps]
    assert step_names == [
        "make_audit", "compute_usage", "maybe_spawn_session_reviewer",
    ], f"finalize-subagent step order drifted: {step_names}"


def test_pipeline_run_spawns_reviewer_when_threshold_met(tmp_path, monkeypatch):
    """End-to-end (pipeline-level): SubAgent finalize → reviewer spawn.

    With turns=5/tool_calls=10 (above threshold 1/1), the pipeline must
    invoke ``_spawn_session_reviewer_background``.

    NB: ``make_audit`` would otherwise overwrite the seeded ``metrics.json``
    based on JSONL parsing (or the trace-log fallback when no JSONL exists,
    which produces ``turns=0``). We neutralise that here by stubbing
    ``AuditWriter._write_metrics`` — the test target is the YAML wiring +
    spawn invocation, not the audit-writer's responsibilities.
    """
    session_dir = _seed_project_with_retro_policy(
        tmp_path,
        min_turns=1,
        min_tool_calls=1,
        metrics={"turns": 5, "tool_calls": 10},
    )

    # Neutralise make_audit's metrics overwrite (it has no JSONL to read
    # and would otherwise reset our seeded metrics to turns=0 via the
    # trace-fallback path).
    monkeypatch.setattr(
        "ai_hats.observe.AuditWriter._write_metrics",
        lambda *a, **kw: None,
    )

    spawned: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.delenv("HATS_SKIP_RETRO", raising=False)

    pipe = load_pipeline(FINALIZE_SUBAGENT_YAML)
    final = run_pipeline(pipe, initial={
        "session_id": "test",
        "session_dir": session_dir,
        "claude_session_id": "fake-cid",  # make_audit's JSONL discovery fails gracefully (failure_policy=continue)
        "project_dir": tmp_path,
        "exit_code": 0,
    })

    assert spawned == [(tmp_path, "test")], (
        f"finalize-subagent did not spawn reviewer; spawned={spawned}"
    )
    # retro_decision lands in the funnel for any future downstream step.
    assert final.get("retro_decision", {}).get("action") == "run"


def test_pipeline_run_no_spawn_below_threshold(tmp_path, monkeypatch):
    """Below threshold (turns=0) → decision is `skip`, no spawn call."""
    session_dir = _seed_project_with_retro_policy(
        tmp_path,
        min_turns=5,
        min_tool_calls=10,
        metrics={"turns": 0, "tool_calls": 0},
    )

    spawned: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.delenv("HATS_SKIP_RETRO", raising=False)

    pipe = load_pipeline(FINALIZE_SUBAGENT_YAML)
    final = run_pipeline(pipe, initial={
        "session_id": "test",
        "session_dir": session_dir,
        "claude_session_id": "fake-cid",
        "project_dir": tmp_path,
        "exit_code": 0,
    })

    assert spawned == [], "no spawn must happen below threshold"
    assert final.get("retro_decision", {}).get("action") == "skip"


def test_pipeline_run_recursion_guard_blocks_spawn(tmp_path, monkeypatch):
    """`HATS_SKIP_RETRO=1` → no spawn even when threshold met.

    Critical for the session-reviewer's OWN finalize (it spawns as a
    SubAgent, runs the same `finalize-subagent` pipeline, and must NOT
    spawn yet another reviewer).
    """
    session_dir = _seed_project_with_retro_policy(
        tmp_path,
        min_turns=1,
        min_tool_calls=1,
        metrics={"turns": 5, "tool_calls": 10},
    )
    monkeypatch.setattr(
        "ai_hats.observe.AuditWriter._write_metrics",
        lambda *a, **kw: None,
    )

    spawned: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.setenv("HATS_SKIP_RETRO", "1")

    pipe = load_pipeline(FINALIZE_SUBAGENT_YAML)
    run_pipeline(pipe, initial={
        "session_id": "test",
        "session_dir": session_dir,
        "claude_session_id": "fake-cid",
        "project_dir": tmp_path,
        "exit_code": 0,
    })

    assert spawned == [], "HATS_SKIP_RETRO must block spawn in pipeline run"
