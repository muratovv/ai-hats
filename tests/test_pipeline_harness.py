"""Unit tests for PipelineHarness (HATS-269 base + HATS-274 trace
wiring + HATS-275 user-step loading)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from ai_hats.pipeline import registry
from ai_hats.pipeline.harness import PipelineHarness
from ai_hats.pipeline.user_steps import _reset_loader_cache


def test_namespace_idempotent_cleanup(tmp_path: Path):
    h = PipelineHarness("execute", tmp_path)
    # leftover from a previous run
    h.namespace.mkdir(parents=True)
    (h.namespace / "leftover.txt").write_text("stale")

    with h:
        assert h.namespace.exists()
        assert not (h.namespace / "leftover.txt").exists()


def test_namespace_per_pipeline_isolated(tmp_path: Path):
    bare = PipelineHarness("human", tmp_path)
    execute = PipelineHarness("execute", tmp_path)
    with bare, execute:
        assert bare.namespace != execute.namespace
        assert bare.namespace.parent == execute.namespace.parent


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
    h = PipelineHarness("my-name", tmp_path)
    assert h.namespace == tmp_path / ".gitlog" / "pipeline_runs" / "my-name"


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
    namespace gets re-created. This guarantees a broken step-dir
    can't half-start the pipeline."""
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

    h = PipelineHarness("any-name", tmp_path)
    # Pre-create namespace with a sentinel; failed __enter__ must NOT
    # rmtree it (because rmtree happens AFTER load_user_steps).
    h.namespace.mkdir(parents=True)
    (h.namespace / "sentinel.txt").write_text("preserved")

    with pytest.raises(registry.StepRegistryError):
        h.__enter__()

    # Sentinel survived → we never reached namespace cleanup.
    assert (h.namespace / "sentinel.txt").read_text() == "preserved"
