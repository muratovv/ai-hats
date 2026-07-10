"""HATS-863 — carry schema + ``parse_worktree_carry`` (moved from
``ai_hats.models`` / ``SkillMetadata._normalize_worktree``; ADR-0012 semantics).

Leaf records (WorktreeHook) are fail-loud (extra=forbid); the ``worktree:``
container is forward-compat (unknown keys WARN + ignored, not a hard fail).
"""

from __future__ import annotations

import pytest

from ai_hats_wt import (
    WT_TEARDOWN_EVENTS,
    WorktreeCarry,
    WorktreeHook,
    parse_worktree_carry,
)


def test_wt_out_with_on() -> None:
    carry = parse_worktree_carry(
        {"wt_out": [{"script": "scripts/drain.sh", "on": ["merge", "discard"]}]}, "demo"
    )
    assert isinstance(carry, WorktreeCarry)
    assert len(carry.wt_out) == 1
    hook = carry.wt_out[0]
    assert isinstance(hook, WorktreeHook)
    assert hook.script == "scripts/drain.sh"
    assert hook.on == ("merge", "discard")


def test_wt_in() -> None:
    carry = parse_worktree_carry({"wt_in": [{"script": "scripts/seed.sh"}]}, "demo")
    assert len(carry.wt_in) == 1
    assert carry.wt_in[0].script == "scripts/seed.sh"
    assert carry.wt_in[0].on == ()


def test_wt_out_default_on_is_all_teardown_events() -> None:
    carry = parse_worktree_carry({"wt_out": [{"script": "scripts/drain.sh"}]}, "demo")
    assert carry.wt_out[0].on == WT_TEARDOWN_EVENTS


def test_wt_out_unknown_event_raises() -> None:
    with pytest.raises(ValueError, match="bogus"):
        parse_worktree_carry(
            {"wt_out": [{"script": "scripts/drain.sh", "on": ["merge", "bogus"]}]}, "demo"
        )


def test_entry_missing_script_raises() -> None:
    with pytest.raises(ValueError, match="script"):
        parse_worktree_carry({"wt_out": [{"on": ["merge"]}]}, "demo")


def test_error_names_the_skill() -> None:
    with pytest.raises(ValueError, match="demo"):
        parse_worktree_carry({"wt_out": "not-a-list"}, "demo")


def test_leaf_extra_field_forbidden() -> None:
    with pytest.raises(ValueError):
        WorktreeHook(script="x.sh", bogus="nope")  # type: ignore[call-arg]


def test_unknown_container_key_warns_and_is_ignored() -> None:
    with pytest.warns(UserWarning, match="future_kind"):
        carry = parse_worktree_carry(
            {
                "wt_out": [{"script": "scripts/drain.sh", "on": ["merge"]}],
                "future_kind": [{"path": ".cache"}],
            },
            "demo",
        )
    # Known kind still parsed; unknown kind dropped (forward-compat).
    assert len(carry.wt_out) == 1


def test_on_yaml_true_key_restored() -> None:
    # PyYAML coerces bare `on:` to the boolean True key; the parser restores it.
    carry = parse_worktree_carry({"wt_out": [{True: ["merge"], "script": "x.sh"}]}, "demo")
    assert carry.wt_out[0].on == ("merge",)


def test_wt_in_with_on_warns_and_is_cleared() -> None:
    with pytest.warns(UserWarning, match="wt_in"):
        carry = parse_worktree_carry(
            {"wt_in": [{"script": "scripts/seed.sh", "on": ["merge"]}]}, "demo"
        )
    assert carry.wt_in[0].on == ()


def test_distinct_scripts_sharing_basename_raise() -> None:
    # Distinct scripts with one basename collide on the materialized filename.
    with pytest.raises(ValueError, match="basename"):
        parse_worktree_carry(
            {"wt_in": [{"script": "a/run.sh"}], "wt_out": [{"script": "b/run.sh"}]}, "demo"
        )


def test_same_script_reused_across_kinds_ok() -> None:
    carry = parse_worktree_carry(
        {"wt_in": [{"script": "hooks/run.sh"}], "wt_out": [{"script": "hooks/run.sh"}]}, "demo"
    )
    assert carry.wt_in[0].script == "hooks/run.sh"
    assert carry.wt_out[0].script == "hooks/run.sh"


@pytest.mark.parametrize("raw", [None, {}, ""])
def test_falsy_raw_empty_carry(raw: object) -> None:
    carry = parse_worktree_carry(raw, "demo")
    assert carry.is_empty()


def test_non_mapping_raw_raises() -> None:
    with pytest.raises(ValueError, match="mapping"):
        parse_worktree_carry(["not", "a", "dict"], "demo")
