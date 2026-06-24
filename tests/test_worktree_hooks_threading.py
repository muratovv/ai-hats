"""HATS-823 — threading helpers: serialize collected hooks for persistence and
degrade gracefully when no role / composition is available."""

from __future__ import annotations

from pathlib import Path

from ai_hats.models import WorktreeHook
from ai_hats.worktree_hooks import collect_carry_for_role, serialize_collected_hooks


def test_serialize_drops_empty_on_for_wt_in():
    collected = {
        "wt_in": [("seeder", WorktreeHook(script="seed.sh"))],
        "wt_out": [("drainer", WorktreeHook(script="drain.sh", on=("merge", "discard")))],
    }
    out = serialize_collected_hooks(collected)
    assert out["wt_in"] == [{"skill": "seeder", "script": "seed.sh"}]
    assert out["wt_out"] == [
        {"skill": "drainer", "script": "drain.sh", "on": ["merge", "discard"]}
    ]


def test_serialize_skips_empty_kinds():
    assert serialize_collected_hooks({}) == {}
    assert serialize_collected_hooks({"wt_in": []}) == {}


def test_collect_carry_for_non_project_is_empty(tmp_path: Path):
    # No ai-hats.yaml / no role → empty carry, no exception (graceful).
    assert collect_carry_for_role(tmp_path) == {}
