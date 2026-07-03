"""HATS-823 — composer.collect_worktree_hooks walks composed skills and groups
their wt_in / wt_out lifecycle hooks by kind (mirrors collect_runtime_hooks)."""

from __future__ import annotations

from pathlib import Path

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent

from ai_hats.composer import collect_worktree_hooks


def _skill(base: Path, name: str, body: str) -> ResolvedComponent:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nai_hats:\n  worktree:\n{body}---\n# {name}\n"
    )
    return ResolvedComponent(
        name=name, component_type=ComponentKind.SKILL, source_path=d
    )


def _result(skills: list[ResolvedComponent]) -> CompositionResult:
    return CompositionResult(
        name="r", priorities=[], rules=[], skills=skills, injections=[]
    )


def test_groups_by_kind_with_skill_attribution(tmp_path: Path) -> None:
    a = _skill(
        tmp_path,
        "alpha",
        "    wt_out:\n      - script: drain.sh\n        on: [merge]\n",
    )
    b = _skill(tmp_path, "beta", "    wt_in:\n      - script: seed.sh\n")
    collected = collect_worktree_hooks(_result([a, b]))
    assert set(collected) == {"wt_in", "wt_out"}
    assert collected["wt_out"][0][0] == "alpha"
    assert collected["wt_out"][0][1].script == "drain.sh"
    assert collected["wt_in"][0][0] == "beta"


def test_multiple_skills_same_kind_accumulate(tmp_path: Path) -> None:
    a = _skill(tmp_path, "alpha", "    wt_out:\n      - script: a.sh\n")
    b = _skill(tmp_path, "beta", "    wt_out:\n      - script: b.sh\n")
    collected = collect_worktree_hooks(_result([a, b]))
    scripts = {(s, h.script) for s, h in collected["wt_out"]}
    assert scripts == {("alpha", "a.sh"), ("beta", "b.sh")}


def test_skill_without_carry_skipped(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "SKILL.md").write_text("---\nname: plain\n---\n# plain\n")
    rc = ResolvedComponent(
        name="plain", component_type=ComponentKind.SKILL, source_path=plain
    )
    assert collect_worktree_hooks(_result([rc])) == {}
