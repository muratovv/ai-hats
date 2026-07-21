"""Tests for the generic ref-counted skills-dir materializer (HATS-993)."""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats_core import ComponentKind, ResolvedComponent

from ai_hats.skills_dir import MANAGED_MARKER, materialize_skills_dir


def _make_skill(name: str, root: Path, body: str = "") -> ResolvedComponent:
    """Build a skill source dir on disk and the matching ResolvedComponent."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body or f"---\nname: {name}\n---\n# {name}\n")
    return ResolvedComponent(
        name=name,
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection=body,
    )


def test_materializes_skill_and_writes_marker(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    target = tmp_path / ".agy" / "skills"

    materialize_skills_dir(target, [skill], tmp_path, "sid-1")

    assert (target / "alpha" / "SKILL.md").is_file()
    refs = json.loads((target / MANAGED_MARKER).read_text())
    assert refs == {"sid-1": ["alpha"]}


def test_role_change_sweeps_unreferenced_skill(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    alpha = _make_skill("alpha", skills_root)
    beta = _make_skill("beta", skills_root)
    target = tmp_path / "skills"

    materialize_skills_dir(target, [alpha], tmp_path, "sid-1")
    materialize_skills_dir(target, [beta], tmp_path, "sid-1")

    assert not (target / "alpha").exists()
    assert (target / "beta" / "SKILL.md").is_file()


def test_parallel_sessions_keep_each_others_skills(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    alpha = _make_skill("alpha", skills_root)
    beta = _make_skill("beta", skills_root)
    target = tmp_path / "skills"

    materialize_skills_dir(target, [alpha], tmp_path, "sid-1")
    materialize_skills_dir(target, [beta], tmp_path, "sid-2")

    assert (target / "alpha" / "SKILL.md").is_file()
    assert (target / "beta" / "SKILL.md").is_file()
    refs = json.loads((target / MANAGED_MARKER).read_text())
    assert refs == {"sid-1": ["alpha"], "sid-2": ["beta"]}


def test_concurrent_threads_both_skill_sets_present(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    skills_root = tmp_path / "src"
    skills_root.mkdir()
    alpha = _make_skill("alpha", skills_root)
    beta = _make_skill("beta", skills_root)
    target = tmp_path / "skills"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(materialize_skills_dir, target, [alpha], tmp_path, "sid-1"),
            pool.submit(materialize_skills_dir, target, [beta], tmp_path, "sid-2"),
        ]
        for f in futures:
            f.result()

    assert (target / "alpha" / "SKILL.md").is_file()
    assert (target / "beta" / "SKILL.md").is_file()


def test_user_authored_dir_untouched_by_sweep(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    alpha = _make_skill("alpha", skills_root)
    target = tmp_path / "skills"
    user_skill = target / "my-own-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("# mine\n")

    materialize_skills_dir(target, [alpha], tmp_path, "sid-1")
    materialize_skills_dir(target, [], tmp_path, "sid-1")

    assert (user_skill / "SKILL.md").is_file()
    assert not (target / "alpha").exists()


def test_expands_placeholder_in_skill_md_only(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    body = "---\nname: alpha\n---\nBase: <ai_hats_dir>/x\n"
    alpha = _make_skill("alpha", skills_root, body=body)
    (alpha.source_path / "asset.txt").write_text("verbatim <ai_hats_dir>\n")
    target = tmp_path / "skills"

    materialize_skills_dir(target, [alpha], tmp_path, "sid-1")

    materialized = (target / "alpha" / "SKILL.md").read_text()
    assert "<ai_hats_dir>" not in materialized
    assert (target / "alpha" / "asset.txt").read_text() == "verbatim <ai_hats_dir>\n"


def test_gitignore_entry_appended_once(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    alpha = _make_skill("alpha", skills_root)
    target = tmp_path / ".agy" / "skills"

    for _ in range(2):
        materialize_skills_dir(
            target, [alpha], tmp_path, "sid-1", gitignore_entry=".agy/skills/"
        )

    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert lines.count(".agy/skills/") == 1


def test_corrupt_marker_starts_fresh(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    alpha = _make_skill("alpha", skills_root)
    target = tmp_path / "skills"
    target.mkdir(parents=True)
    (target / MANAGED_MARKER).write_text("not json{")

    materialize_skills_dir(target, [alpha], tmp_path, "sid-1")

    refs = json.loads((target / MANAGED_MARKER).read_text())
    assert refs == {"sid-1": ["alpha"]}
