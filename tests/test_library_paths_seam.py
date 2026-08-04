"""HATS-1269 S1: the library-path list has ONE builder, shared by two callers.

Worktree teardown must resolve a hook's declaring skill without a
``CompositionResult`` and without paying for a full ``Assembler``. It therefore
needs the same ordered library roots the assembler composes against — and a
second, hand-rolled copy of that ordering would drift silently, resolving a
different skill at teardown than composition ever saw.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.library_paths import build_library_paths
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def _make_project(root: Path) -> ProjectConfig:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    (root / "libraries" / "skills" / "demo").mkdir(parents=True)
    (root / "libraries" / "skills" / "demo" / "SKILL.md").write_text("demo\n")
    config = ProjectConfig(provider="agy")
    config.save(root / PROJECT_CONFIG)
    return config


def test_assembler_delegates_to_the_shared_builder(tmp_path, monkeypatch):
    """What the assembler resolves against IS what the shared builder returns."""
    proj = tmp_path / "repo"
    config = _make_project(proj)
    monkeypatch.chdir(proj)

    assert Assembler(proj).library_paths == build_library_paths(
        proj, config_paths=config.library_paths
    )
