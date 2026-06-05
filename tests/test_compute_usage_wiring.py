"""Pipeline-load wiring test for ``compute_usage`` (HATS-664).

Asserts the step is registered and wired right after ``make_audit`` in BOTH
finalize pipelines (HITL + SubAgent) — the JSONL is read once by ``make_audit``
for ``audit.md`` and again by ``compute_usage`` for ``usage.json``; both live on
the same post-session source so they belong adjacent.

Loaded from the worktree's YAML by explicit path (NOT ``load_core_pipeline``,
which resolves to the installed package and would mask local edits).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.pipeline import registry
from ai_hats.pipeline.loader import load_pipeline

PIPELINES = Path(__file__).resolve().parent.parent / "library" / "core" / "pipelines"


def test_compute_usage_registered():
    assert "compute_usage" in registry.names()


def test_compute_usage_after_make_audit_in_hitl():
    names = [s.io.name for s in load_pipeline(PIPELINES / "finalize-hitl.yaml").steps]
    assert "compute_usage" in names
    assert names.index("compute_usage") == names.index("make_audit") + 1


def test_compute_usage_after_make_audit_in_subagent():
    names = [s.io.name for s in load_pipeline(PIPELINES / "finalize-subagent.yaml").steps]
    assert "compute_usage" in names
    assert names.index("compute_usage") == names.index("make_audit") + 1
