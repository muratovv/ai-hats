"""HATS-831: project-local ``libraries/`` re-points to the worktree (topology B).

A downstream project customizes agent behavior via a git-tracked project-local
``libraries/``. Inside a linked worktree, ``_project_dir`` hops to MAIN (HATS-524,
to share the tracker), so the project-local layer would resolve to ``MAIN/libraries``
— invisible to edits the agent makes in the worktree. The assembler re-points ONLY
that layer to the worktree's own toplevel (``WorktreeManager.worktree_toplevel``).

Two contracts pinned here against real git:
  1. ``worktree_toplevel`` returns the worktree's OWN root (mirror image of
     ``main_worktree_root``, which hops to MAIN).
  2. ``Assembler._build_library_paths`` picks ``<worktree>/libraries`` when cwd is
     inside the worktree, ``<project_dir>/libraries`` otherwise.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats_wt import WorktreeManager


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "t@t.co")
    _git(path, "config", "user.name", "t")


def _make_downstream_project(root: Path) -> None:
    """A downstream project: ai-hats.yaml + a git-tracked project-local libraries/."""
    _init_repo(root)
    (root / "libraries" / "skills" / "demo").mkdir(parents=True)
    (root / "libraries" / "skills" / "demo" / "SKILL.md").write_text("main copy\n")
    ProjectConfig(provider="gemini").save(root / "ai-hats.yaml")
    _git(root, "add", "libraries")  # ai-hats.yaml is gitignored by convention
    _git(root, "commit", "-m", "init", "--no-verify")


# ---- worktree_toplevel contract (real git) ---------------------------------


def test_worktree_toplevel_none_in_main_repo(tmp_path):
    proj = tmp_path / "repo"
    _make_downstream_project(proj)
    assert WorktreeManager.worktree_toplevel(proj) is None


def test_worktree_toplevel_none_for_non_git(tmp_path):
    assert WorktreeManager.worktree_toplevel(tmp_path) is None


def test_worktree_toplevel_returns_own_root_not_main(tmp_path):
    # The key contrast with main_worktree_root: this returns the LINKED
    # worktree's own root, while main_worktree_root hops the other way (MAIN).
    proj = tmp_path / "repo"
    _make_downstream_project(proj)
    wt = tmp_path / "linked"
    _git(proj, "worktree", "add", str(wt))

    own = WorktreeManager.worktree_toplevel(wt)
    main = WorktreeManager.main_worktree_root(wt)
    assert own is not None and own.resolve() == wt.resolve()
    assert main is not None and main.resolve() == proj.resolve()
    assert own != main  # they point in opposite directions


# ---- Assembler project-local re-point (end-to-end) -------------------------


def test_project_local_libraries_repoint_in_worktree(tmp_path, monkeypatch):
    # Agent edits libraries/** in the worktree; composition must read THAT copy.
    proj = tmp_path / "repo"
    _make_downstream_project(proj)
    wt = tmp_path / "linked"
    _git(proj, "worktree", "add", str(wt))
    (wt / "libraries" / "skills" / "demo" / "SKILL.md").write_text("worktree edit\n")

    # cwd inside the worktree → _project_dir would hop to MAIN, so we pass the
    # MAIN root as project_dir (mirroring reality) and assert the layer re-points.
    monkeypatch.chdir(wt)
    asm = Assembler(proj)

    assert (wt / "libraries") in asm.library_paths
    assert (proj / "libraries") not in asm.library_paths


def test_project_local_libraries_main_when_not_in_worktree(tmp_path, monkeypatch):
    # Control: from the MAIN checkout there is no re-point — the project-local
    # layer is project_dir/libraries.
    proj = tmp_path / "repo"
    _make_downstream_project(proj)

    monkeypatch.chdir(proj)
    asm = Assembler(proj)

    assert (proj / "libraries") in asm.library_paths


def test_no_git_probe_when_cwd_under_project_dir(tmp_path, monkeypatch):
    # Regression (HATS-831): composing from within project_dir — the common
    # main-checkout path, and every subprocess-mocking pipeline test — must NOT
    # shell out to git. The cheap fs pre-gate short-circuits before any
    # WorktreeManager call.
    proj = tmp_path / "repo"
    _make_downstream_project(proj)

    def _boom(*a, **k):
        raise AssertionError("git probe must not run when cwd is under project_dir")

    monkeypatch.setattr(WorktreeManager, "main_worktree_root", staticmethod(_boom))
    monkeypatch.setattr(WorktreeManager, "worktree_toplevel", staticmethod(_boom))
    monkeypatch.chdir(proj)

    asm = Assembler(proj)
    assert (proj / "libraries") in asm.library_paths


def test_only_the_libraries_layer_repoints_everything_else_stays_main(tmp_path, monkeypatch):
    # Boundary (HATS-831 R3): re-pointing is surgical — the gitignored
    # ai-hats.yaml overlay, the agent dir, and config-specified library paths
    # all stay resolved against MAIN even when cwd is inside the worktree.
    proj = tmp_path / "repo"
    _make_downstream_project(proj)
    wt = tmp_path / "linked"
    _git(proj, "worktree", "add", str(wt))

    monkeypatch.chdir(wt)
    asm = Assembler(proj)

    assert asm.config_path == proj / "ai-hats.yaml"  # overlay stays MAIN
    assert asm.agent_dir == proj / ".agent"  # tracker/sessions stay MAIN
    assert asm.project_dir == proj  # the hop target is untouched
