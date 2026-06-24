"""HATS-823 — the assembler materializes skill-declared worktree hook scripts
(wt_in / wt_out) to ``<ai_hats_dir>/library/wt-hooks/``: managed dir + manifest
+ sweep, mirroring runtime-hook materialization. Managed-only; a project with no
wt hooks gets no dir."""

import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.composer import CompositionResult, ResolvedComponent
from ai_hats.models import ComponentType
from ai_hats.paths import managed_wt_hook_filename, wt_hooks_dir


def _skill_with_wt(
    base: Path, name: str, body: str, scripts: list[str]
) -> ResolvedComponent:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nai_hats:\n  worktree:\n{body}---\n# {name}\n"
    )
    for rel in scripts:
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/usr/bin/env bash\nexit 0\n")
        p.chmod(0o755)
    return ResolvedComponent(
        name=name, component_type=ComponentType.SKILL, source_path=d
    )


@pytest.fixture
def assembler(tmp_path: Path) -> Assembler:
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    return Assembler(project_dir=project)


def _result(skills: list[ResolvedComponent]) -> CompositionResult:
    return CompositionResult(
        name="r", priorities=[], rules=[], skills=skills, injections=[]
    )


def _dest(assembler: Assembler, skill: str, script: str) -> Path:
    return wt_hooks_dir(assembler.project_dir) / managed_wt_hook_filename(
        skill, script
    )


def test_materializes_executable_script(assembler, tmp_path):
    s = _skill_with_wt(
        tmp_path / "skills",
        "drainer",
        "    wt_out:\n      - script: hooks/drain.sh\n        on: [merge]\n",
        ["hooks/drain.sh"],
    )
    assembler._materialize_worktree_hooks(_result([s]))
    dest = _dest(assembler, "drainer", "hooks/drain.sh")
    assert dest.is_file()
    assert dest.stat().st_mode & 0o111


def test_idempotent_rerun(assembler, tmp_path):
    s = _skill_with_wt(
        tmp_path / "skills",
        "drainer",
        "    wt_in:\n      - script: seed.sh\n",
        ["seed.sh"],
    )
    assembler._materialize_worktree_hooks(_result([s]))
    assembler._materialize_worktree_hooks(_result([s]))  # must not raise
    assert _dest(assembler, "drainer", "seed.sh").is_file()


def test_sweeps_when_skill_leaves_composition(assembler, tmp_path):
    s = _skill_with_wt(
        tmp_path / "skills",
        "drainer",
        "    wt_out:\n      - script: drain.sh\n",
        ["drain.sh"],
    )
    assembler._materialize_worktree_hooks(_result([s]))
    dest = _dest(assembler, "drainer", "drain.sh")
    assert dest.is_file()
    assembler._materialize_worktree_hooks(_result([]))
    assert not dest.exists()


def test_no_dir_when_no_wt_hooks(assembler, tmp_path):
    plain = tmp_path / "skills" / "plain"
    plain.mkdir(parents=True)
    (plain / "SKILL.md").write_text("---\nname: plain\n---\n# plain\n")
    rc = ResolvedComponent(
        name="plain", component_type=ComponentType.SKILL, source_path=plain
    )
    assembler._materialize_worktree_hooks(_result([rc]))
    assert not wt_hooks_dir(assembler.project_dir).exists()
