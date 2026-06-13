"""Unit tests for PipelineHarness (HATS-269 base + HATS-274 trace
wiring + HATS-275 user-step loading + HATS-308 per-session namespace)."""

from __future__ import annotations

import json
import os
import textwrap
import time
from pathlib import Path

import pytest

from ai_hats.pipeline import registry
from ai_hats.pipeline.harness import PipelineHarness
from ai_hats.pipeline.user_steps import _reset_loader_cache
from ai_hats.paths import runs_dir


def test_old_sessions_pruned(tmp_path: Path, monkeypatch):
    """Keep-N retention prunes the oldest sibling session dirs."""
    monkeypatch.setenv("AI_HATS_PIPELINE_KEEP_N", "3")
    pipeline_root = runs_dir(tmp_path) / "pipeline_runs" / "execute"
    pipeline_root.mkdir(parents=True)
    # Pre-create 5 old sibling sessions; force mtime order (oldest first).
    for i in range(5):
        (pipeline_root / f"old-{i:02d}").mkdir()
    for i in range(5):
        t = time.time() - (5 - i) * 60
        os.utime(pipeline_root / f"old-{i:02d}", (t, t))

    h = PipelineHarness("execute", tmp_path, session_id="new-sid")
    with h:
        remaining = sorted(
            p.name for p in pipeline_root.iterdir() if p.is_dir()
        )
    # keep_n=3 → 2 most recent siblings + this run's dir.
    assert "new-sid" in remaining
    # old-03 and old-04 are the 2 newest → kept.
    assert "old-03" in remaining
    assert "old-04" in remaining
    # old-00, old-01, old-02 — pruned.
    for stale in ("old-00", "old-01", "old-02"):
        assert stale not in remaining


def test_namespace_per_pipeline_isolated(tmp_path: Path):
    bare = PipelineHarness("human", tmp_path)
    execute = PipelineHarness("execute", tmp_path)
    with bare, execute:
        assert bare.namespace != execute.namespace
        # Different pipeline names → different parent dirs.
        assert bare.namespace.parent != execute.namespace.parent
        # But both under the same pipeline_runs root.
        assert bare.namespace.parent.parent == execute.namespace.parent.parent


def test_parallel_runs_disjoint(tmp_path: Path):
    """Two harnesses of the SAME pipeline name get disjoint namespaces."""
    h1 = PipelineHarness("p", tmp_path, session_id="sid1")
    h2 = PipelineHarness("p", tmp_path, session_id="sid2")
    with h1, h2:
        (h1.namespace / "a.txt").write_text("from h1")
        (h2.namespace / "b.txt").write_text("from h2")
        assert (h1.namespace / "a.txt").read_text() == "from h1"
        assert (h2.namespace / "b.txt").read_text() == "from h2"
        assert h1.namespace != h2.namespace
        assert h1.namespace.parent == h2.namespace.parent


def test_explicit_session_id_passthrough(tmp_path: Path):
    h = PipelineHarness("p", tmp_path, session_id="MYSID")
    assert h.session_id == "MYSID"
    assert h.namespace.name == "MYSID"


def test_auto_session_id_format(tmp_path: Path):
    h = PipelineHarness("p", tmp_path)
    # Format: YYYYMMDDTHHMMSS-XXX (8+1+6+1+3 = 19 chars).
    assert len(h.session_id) == 19
    assert h.session_id[8] == "T"
    assert h.session_id[15] == "-"


def test_materialize_prompt_writes_file(tmp_path: Path):
    with PipelineHarness("execute", tmp_path) as h:
        p = h.materialize_prompt("hello world")
        assert p is not None
        assert p.read_text() == "hello world"
        assert p.parent == h.namespace


def test_materialize_prompt_none(tmp_path: Path):
    with PipelineHarness("execute", tmp_path) as h:
        assert h.materialize_prompt(None) is None


def test_materialize_prompt_empty_string(tmp_path: Path):
    with PipelineHarness("execute", tmp_path) as h:
        assert h.materialize_prompt("") is None


def test_run_loads_yaml_and_executes(tmp_path: Path):
    """Single-step reflect-session pipeline runs end-to-end via harness."""
    from unittest.mock import patch

    fake_path = tmp_path / "review.md"
    with patch(
        "ai_hats.retro.session_review_runner.SessionReviewRunner",
    ) as MockRunner:
        MockRunner.return_value.run.return_value = fake_path
        with PipelineHarness("reflect-session", tmp_path) as h:
            final = h.run({
                "session_id": "x-1",
                "project_dir": tmp_path,
            })
    assert final["review_path"] == fake_path


def test_namespace_path_layout(tmp_path: Path):
    h = PipelineHarness("my-name", tmp_path, session_id="testsid-001")
    assert h.namespace == (
        runs_dir(tmp_path) / "pipeline_runs" / "my-name" / "testsid-001"
    )


# ---- HATS-274: trace-mode env wiring -------------------------------


def test_harness_no_trace_when_env_unset(tmp_path: Path, monkeypatch):
    """Default: env not set → on_step is None, trace_path is None."""
    monkeypatch.delenv("AI_HATS_PIPELINE_TRACE", raising=False)
    monkeypatch.delenv("AI_HATS_PIPELINE_TRACE_VALUES", raising=False)
    h = PipelineHarness("execute", tmp_path)
    assert h._on_step is None
    assert h.trace_path is None
    assert h._trace_values is False


def test_harness_enables_trace_via_explicit_path(tmp_path: Path, monkeypatch):
    """AI_HATS_PIPELINE_TRACE=<path>.jsonl → uses that file."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    explicit = tmp_path / "my-trace.jsonl"
    monkeypatch.setenv("AI_HATS_PIPELINE_TRACE", str(explicit))
    h = PipelineHarness("execute", tmp_path)
    assert h.trace_path == explicit
    assert h._on_step is not None


def test_harness_enables_trace_in_default_location(tmp_path: Path, monkeypatch):
    """Truthy non-.jsonl value → auto path under <traces_dir>."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setenv("AI_HATS_PIPELINE_TRACE", "1")
    h = PipelineHarness("bare", tmp_path)
    assert h.trace_path is not None
    # Auto-named: lives under .agent/ai-hats/traces/, named for pipeline.
    expected_dir = tmp_path / ".agent" / "ai-hats" / "traces"
    assert h.trace_path.parent == expected_dir
    assert h.trace_path.name.startswith("bare-")
    assert h.trace_path.suffix == ".jsonl"


def test_harness_respects_ai_hats_dir_override(tmp_path: Path, monkeypatch):
    """AI_HATS_DIR cascades into traces_dir resolution."""
    custom = tmp_path / "custom-runtime"
    monkeypatch.setenv("AI_HATS_DIR", str(custom))
    monkeypatch.setenv("AI_HATS_PIPELINE_TRACE", "auto")
    h = PipelineHarness("reflect-all", tmp_path)
    assert h.trace_path is not None
    assert h.trace_path.parent == custom / "traces"


def test_harness_trace_values_env_var(tmp_path: Path, monkeypatch):
    """AI_HATS_PIPELINE_TRACE_VALUES=1 → events carry value reprs."""
    monkeypatch.setenv("AI_HATS_PIPELINE_TRACE_VALUES", "1")
    h = PipelineHarness("execute", tmp_path)
    assert h._trace_values is True


def test_harness_trace_values_off_for_falsy_strings(tmp_path: Path, monkeypatch):
    """``0``/``false`` are NOT truthy."""
    for val in ("", "0", "false", "False"):
        monkeypatch.setenv("AI_HATS_PIPELINE_TRACE_VALUES", val)
        h = PipelineHarness("execute", tmp_path)
        assert h._trace_values is False, f"value={val!r} should be falsy"


def test_harness_writes_trace_file_on_run(tmp_path: Path, monkeypatch):
    """End-to-end smoke: env set + harness.run → JSONL file populated."""
    from unittest.mock import patch

    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("AI_HATS_PIPELINE_TRACE", str(trace_path))

    fake_path = tmp_path / "review.md"
    with patch(
        "ai_hats.retro.session_review_runner.SessionReviewRunner",
    ) as MockRunner:
        MockRunner.return_value.run.return_value = fake_path
        with PipelineHarness("reflect-session", tmp_path) as h:
            h.run({"session_id": "x-1", "project_dir": tmp_path})

    assert trace_path.exists()
    lines = trace_path.read_text().splitlines()
    assert len(lines) >= 1
    parsed = [json.loads(line) for line in lines]
    assert any(e["step"] == "run_session_review" for e in parsed)


# ---- HATS-275: user-step loading on harness entry ------------------


@pytest.fixture
def _restore_registry():
    """Snapshot/restore registry around tests that mutate it."""
    _reset_loader_cache()
    snapshot = dict(registry._REGISTRY)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(snapshot)


def _write_step(steps_dir: Path, name: str, body: str) -> Path:
    steps_dir.mkdir(parents=True, exist_ok=True)
    path = steps_dir / f"{name}.py"
    path.write_text(textwrap.dedent(body).lstrip())
    return path


def test_harness_loads_user_steps_on_enter(
    tmp_path: Path, monkeypatch, _restore_registry,
):
    """__enter__ → user step is registered and discoverable."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    steps_dir = tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    _write_step(steps_dir, "ping", """
        from ai_hats.pipeline.registry import register
        from ai_hats.pipeline.step import Step, StepIO


        class PingStep(Step):
            failure_policy = "halt"

            def __init__(self, params=None):
                del params

            @property
            def io(self) -> StepIO:
                return StepIO(name="ping", produces=frozenset({"pong"}))

            def run(self, **_):
                return {"pong": True}


        register("ping", PingStep)
    """)

    assert "ping" not in registry.names()  # not loaded yet
    with PipelineHarness("any-name", tmp_path):
        assert "ping" in registry.names()


def test_harness_user_step_collision_aborts_before_namespace_setup(
    tmp_path: Path, monkeypatch, _restore_registry,
):
    """A user step trying to override a built-in raises BEFORE the
    per-session dir is created. Namespace must not appear on disk."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    steps_dir = tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    _write_step(steps_dir, "evil", """
        from ai_hats.pipeline.registry import register
        from ai_hats.pipeline.step import Step, StepIO


        class Hijack(Step):
            failure_policy = "halt"

            def __init__(self, params=None):
                del params

            @property
            def io(self) -> StepIO:
                return StepIO(name="compose_role")

            def run(self, **_):
                return {}


        register("compose_role", Hijack)
    """)

    h = PipelineHarness("any-name", tmp_path, session_id="ssid")
    with pytest.raises(registry.StepRegistryError):
        h.__enter__()
    # Per-session dir never created — namespace setup didn't reach mkdir.
    assert not h.namespace.exists()
