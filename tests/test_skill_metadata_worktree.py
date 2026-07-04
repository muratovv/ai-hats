"""HATS-823 / HATS-863 — the ``worktree:`` frontmatter block rides **opaque**
through SkillMetadata (ADR-0014 §2 boundary rule: library never imports wt
types); the compose-time chokepoint ``composer.collect_worktree_hooks`` parses
it via ``ai_hats_wt.parse_worktree_carry`` and fails loud on a malformed row.

Parse/normalization semantics themselves are covered in
``packages/ai-hats-wt/tests/test_carry.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.hook_collection import collect_worktree_hooks
from ai_hats.models import LeftoverSidecarHooksError, SkillMetadata
from ai_hats.skill_sidecar import leftover_sidecar_remedy


def _skill(tmp_path: Path, frontmatter: str, *, sidecar: str | None = None) -> Path:
    d = tmp_path / "skills" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(frontmatter)
    if sidecar is not None:
        (d / "metadata.yaml").write_text(sidecar)
    return d


def _result_with(skill_dir: Path) -> CompositionResult:
    return CompositionResult(
        name="role",
        priorities=[],
        rules=[],
        skills=[
            ResolvedComponent(
                name=skill_dir.name, component_type=ComponentKind.SKILL, source_path=skill_dir
            )
        ],
        injections=[],
    )


def test_worktree_block_rides_opaque(tmp_path: Path) -> None:
    # SkillMetadata does NOT validate the block — even a malformed one loads
    # as a raw dict (the typed parse belongs to the integrator chokepoint).
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
    md = SkillMetadata.from_skill_dir(d)
    assert isinstance(md.worktree, dict)
    assert md.worktree["wt_out"][0]["script"] == "scripts/drain.sh"


def test_no_worktree_block_empty_dict(tmp_path: Path) -> None:
    d = _skill(tmp_path, "---\nname: demo\ndescription: x\n---\n# Demo\n")
    md = SkillMetadata.from_skill_dir(d)
    assert md.worktree == {}


def test_collect_parses_and_groups(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  worktree:\n"
        "    wt_out:\n"
        "      - script: scripts/drain.sh\n"
        "        on: [merge]\n"
        "---\n"
        "# Demo\n",
    )
    collected = collect_worktree_hooks(_result_with(d))
    assert list(collected) == ["wt_out"]
    skill_name, hook = collected["wt_out"][0]
    assert skill_name == "demo"
    assert hook.script == "scripts/drain.sh"
    assert hook.on == ("merge",)


def test_malformed_carry_fails_loud_at_compose_time(tmp_path: Path) -> None:
    # AC-4 parity (HATS-863): the fail-loud moved from SkillMetadata parse to
    # the compose-time chokepoint — NOT deferred to `wt create`.
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
    with pytest.raises(ValueError, match="demo.*bogus|bogus"):
        collect_worktree_hooks(_result_with(d))


def test_leftover_sidecar_with_worktree_raises(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\nname: demo\n---\n# Demo\n",
        sidecar=("name: demo\nworktree:\n  wt_out:\n    - script: scripts/drain.sh\n"),
    )
    with pytest.raises(LeftoverSidecarHooksError) as exc:
        SkillMetadata.from_skill_dir(d)
    assert str(exc.value) == leftover_sidecar_remedy("demo", ["worktree"])
