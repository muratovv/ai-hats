"""Pipeline-wiring + step-driver tests for ``quorum_autoclose`` (HATS-769).

Asserts the step is registered, wired into ``finalize-hitl`` only (HITL-only by
design — see ADR-0008 / plan Out-of-scope), and that the thin pipeline driver
resolves the store from ``project_dir`` and closes a quorum-reached HYP.

Pipelines are loaded from the worktree YAML by explicit path (NOT
``load_core_pipeline``, which resolves to the installed package and would mask
local edits) — same convention as ``test_compute_usage_wiring``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_hats.paths import hypotheses_dir
from ai_hats.pipeline import registry
from ai_hats.pipeline.loader import load_pipeline
from ai_hats.pipeline.steps.quorum_autoclose import QuorumAutoclose

PIPELINES = Path(__file__).resolve().parent.parent / "library" / "core" / "pipelines"


def test_step_registered():
    assert "quorum_autoclose" in registry.names()


def test_wired_into_finalize_hitl():
    names = [s.io.name for s in load_pipeline(PIPELINES / "finalize-hitl.yaml").steps]
    assert "quorum_autoclose" in names


def test_absent_from_finalize_subagent():
    # v1 fires on user (HITL) sessions only; subagent parity is a follow-up.
    names = [s.io.name for s in load_pipeline(PIPELINES / "finalize-subagent.yaml").steps]
    assert "quorum_autoclose" not in names


def test_yaml_param_k_reaches_step():
    """The YAML `params: {k: 3}` is actually applied to the constructed step.

    Guards against a param-key typo silently falling back to the default
    (which equals 3, so only an instance-level assertion catches the revert).
    """
    steps = load_pipeline(PIPELINES / "finalize-hitl.yaml").steps
    step = next(s for s in steps if s.io.name == "quorum_autoclose")
    assert step.k == 3


def test_failure_policy_is_continue():
    # A sweep error must never orphan session finalization.
    assert QuorumAutoclose().failure_policy == "continue"


def test_rejects_k_below_one():
    with pytest.raises(ValueError):
        QuorumAutoclose({"k": 0})


def _write_quorum_hyp(pd: Path) -> None:
    d = hypotheses_dir(pd)
    d.mkdir(parents=True)
    body = {
        "id": "HYP-001",
        "title": "t",
        "status": "active",
        "created": "2026-01-01",
        "source_task": "HATS-001",
        "hypothesis": "h",
        "validation_log": [
            {"date": "2026-06-10", "verdict": "refuted", "evidence": "gone", "session_id": s}
            for s in ("s1", "s2", "s3")
        ],
    }
    (d / "HYP-001.yaml").write_text(yaml.safe_dump(body))


def test_step_closes_quorum_hyp(tmp_path: Path):
    pd = tmp_path / "proj"
    _write_quorum_hyp(pd)

    delta = QuorumAutoclose({"k": 3}).run(project_dir=pd)

    assert delta == {"quorum_closed_hyps": ["HYP-001"]}
    data = yaml.safe_load((hypotheses_dir(pd) / "HYP-001.yaml").read_text())
    assert data["status"] == "refuted"


def test_step_emits_empty_delta_when_nothing_closes(tmp_path: Path):
    pd = tmp_path / "proj"
    hypotheses_dir(pd).mkdir(parents=True)  # empty store
    assert QuorumAutoclose({"k": 3}).run(project_dir=pd) == {}
