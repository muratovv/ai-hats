"""HATS-823 — SkillMetadata reads worktree lifecycle hooks (wt_in / wt_out)
from SKILL.md frontmatter ``ai_hats: worktree:`` per ADR-0012.

Leaf records (WorktreeHook) are fail-loud (extra=forbid); the ``worktree:``
container is forward-compat (unknown keys WARN + ignored, not a hard fail).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.models import (
    WT_TEARDOWN_EVENTS,
    LeftoverSidecarHooksError,
    SkillMetadata,
    WorktreeCarry,
    WorktreeHook,
)
from ai_hats.skill_sidecar import leftover_sidecar_remedy


def _skill(tmp_path: Path, frontmatter: str, *, sidecar: str | None = None) -> Path:
    d = tmp_path / "skills" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(frontmatter)
    if sidecar is not None:
        (d / "metadata.yaml").write_text(sidecar)
    return d


def test_reads_wt_out_with_on(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_out:\n"
        "      - script: scripts/drain.sh\n"
        "        on: [merge, discard]\n"
        "---\n"
        "# Demo\n",
    )
    md = SkillMetadata.from_skill_dir(d)
    assert isinstance(md.worktree, WorktreeCarry)
    assert len(md.worktree.wt_out) == 1
    hook = md.worktree.wt_out[0]
    assert isinstance(hook, WorktreeHook)
    assert hook.script == "scripts/drain.sh"
    assert hook.on == ("merge", "discard")


def test_reads_wt_in(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_in:\n"
        "      - script: scripts/seed.sh\n"
        "---\n"
        "# Demo\n",
    )
    md = SkillMetadata.from_skill_dir(d)
    assert len(md.worktree.wt_in) == 1
    assert md.worktree.wt_in[0].script == "scripts/seed.sh"
    assert md.worktree.wt_in[0].on == ()


def test_wt_out_default_on_is_all_teardown_events(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_out:\n"
        "      - script: scripts/drain.sh\n"
        "---\n"
        "# Demo\n",
    )
    md = SkillMetadata.from_skill_dir(d)
    assert md.worktree.wt_out[0].on == WT_TEARDOWN_EVENTS


def test_wt_out_unknown_event_raises(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_out:\n"
        "      - script: scripts/drain.sh\n"
        "        on: [merge, bogus]\n"
        "---\n"
        "# Demo\n",
    )
    with pytest.raises(ValueError, match="bogus"):
        SkillMetadata.from_skill_dir(d)


def test_entry_missing_script_raises(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_out:\n"
        "      - on: [merge]\n"
        "---\n"
        "# Demo\n",
    )
    with pytest.raises(ValueError, match="script"):
        SkillMetadata.from_skill_dir(d)


def test_leaf_extra_field_forbidden() -> None:
    with pytest.raises(ValueError):
        WorktreeHook(script="x.sh", bogus="nope")  # type: ignore[call-arg]


def test_unknown_container_key_warns_and_is_ignored(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_out:\n"
        "      - script: scripts/drain.sh\n"
        "        on: [merge]\n"
        "    future_kind:\n"
        "      - path: .cache\n"
        "---\n"
        "# Demo\n",
    )
    with pytest.warns(UserWarning, match="future_kind"):
        md = SkillMetadata.from_skill_dir(d)
    # Known kind still parsed; unknown kind dropped (forward-compat).
    assert len(md.worktree.wt_out) == 1


def test_on_yaml_true_key_restored_directly() -> None:
    # PyYAML coerces bare `on:` to the boolean True key; the validator restores
    # the string field even when the row is fed directly (not via YAML text).
    md = SkillMetadata.model_validate(
        {"name": "d", "worktree": {"wt_out": [{True: ["merge"], "script": "x.sh"}]}}
    )
    assert md.worktree.wt_out[0].on == ("merge",)


def test_wt_in_with_on_warns_and_is_cleared(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_in:\n"
        "      - script: scripts/seed.sh\n"
        "        on: [merge]\n"
        "---\n"
        "# Demo\n",
    )
    with pytest.warns(UserWarning, match="wt_in"):
        md = SkillMetadata.from_skill_dir(d)
    assert md.worktree.wt_in[0].on == ()


def test_distinct_scripts_sharing_basename_raise(tmp_path: Path) -> None:
    # Two distinct scripts with the same basename collide on the materialized
    # filename (<skill>-<basename>) — fail loud, like runtime_hooks.
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_in:\n"
        "      - script: a/run.sh\n"
        "    wt_out:\n"
        "      - script: b/run.sh\n"
        "---\n"
        "# Demo\n",
    )
    with pytest.raises(ValueError, match="basename"):
        SkillMetadata.from_skill_dir(d)


def test_same_script_reused_across_kinds_ok(tmp_path: Path) -> None:
    # One file used for both wt_in and wt_out is fine (single materialized file).
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_in:\n"
        "      - script: hooks/run.sh\n"
        "    wt_out:\n"
        "      - script: hooks/run.sh\n"
        "---\n"
        "# Demo\n",
    )
    md = SkillMetadata.from_skill_dir(d)  # must not raise
    assert md.worktree.wt_in[0].script == "hooks/run.sh"
    assert md.worktree.wt_out[0].script == "hooks/run.sh"


def test_no_worktree_block_empty_carry(tmp_path: Path) -> None:
    d = _skill(tmp_path, "---\nname: demo\ndescription: x\n---\n# Demo\n")
    md = SkillMetadata.from_skill_dir(d)
    assert md.worktree.wt_in == ()
    assert md.worktree.wt_out == ()
    assert md.worktree.is_empty()


def test_leftover_sidecar_with_worktree_raises(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\nname: demo\n---\n# Demo\n",
        sidecar=(
            "name: demo\nworktree:\n  wt_out:\n"
            "    - script: scripts/drain.sh\n"
        ),
    )
    with pytest.raises(LeftoverSidecarHooksError) as exc:
        SkillMetadata.from_skill_dir(d)
    assert str(exc.value) == leftover_sidecar_remedy("demo", ["worktree"])
