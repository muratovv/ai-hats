"""Unit tests for PipelineHarness (HATS-269)."""

from __future__ import annotations

from pathlib import Path

from ai_hats.pipeline.harness import PipelineHarness


def test_namespace_idempotent_cleanup(tmp_path: Path):
    h = PipelineHarness("execute", tmp_path)
    # leftover from a previous run
    h.namespace.mkdir(parents=True)
    (h.namespace / "leftover.txt").write_text("stale")

    with h:
        assert h.namespace.exists()
        assert not (h.namespace / "leftover.txt").exists()


def test_namespace_per_pipeline_isolated(tmp_path: Path):
    bare = PipelineHarness("bare", tmp_path)
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
