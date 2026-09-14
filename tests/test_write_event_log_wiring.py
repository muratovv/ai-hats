"""Pipeline-load wiring test for ``write_event_log`` (HATS-1966).

Asserts the step is registered and wired right after ``compute_usage`` in BOTH
finalize pipelines (HITL + SubAgent) — the two are siblings over one transcript,
read for different artifacts, so they belong adjacent.

Loaded from the worktree's YAML by explicit path (NOT ``load_core_pipeline``,
which resolves to the installed package and would mask local edits).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.pipeline import registry
from ai_hats.pipeline.loader import load_pipeline

PIPELINES = (
    Path(__file__).resolve().parent.parent
    / "packages"
    / "ai-hats-library"
    / "src"
    / "ai_hats_library"
    / "core"
    / "pipelines"
)


def test_write_event_log_registered():
    assert "write_event_log" in registry.names()


def test_write_event_log_after_compute_usage_in_hitl():
    names = [s.io.name for s in load_pipeline(PIPELINES / "finalize-hitl.yaml").steps]
    assert "write_event_log" in names
    assert names.index("write_event_log") == names.index("compute_usage") + 1


def test_write_event_log_after_compute_usage_in_subagent():
    names = [s.io.name for s in load_pipeline(PIPELINES / "finalize-subagent.yaml").steps]
    assert "write_event_log" in names
    assert names.index("write_event_log") == names.index("compute_usage") + 1
