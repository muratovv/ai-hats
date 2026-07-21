"""Tests for Assembler.relocate (HATS-366)."""

from __future__ import annotations

import pytest
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def _make_project(tmp_path: Path, *, ai_hats_dir: str = ".agent/ai-hats", venv_path: str | None = None, manage_gitignore: bool = True) -> Path:
    """Build a minimal project with framework state under ai_hats_dir."""
    project = tmp_path / "project"
    project.mkdir()

    config = ProjectConfig(
        provider="agy",
        ai_hats_dir=ai_hats_dir,
        venv_path=venv_path,
        manage_gitignore=manage_gitignore,
    )
    config.save(project / PROJECT_CONFIG)

    # Populate the ai_hats_dir with realistic content.
    base = project / ai_hats_dir
    (base / "library" / "rules").mkdir(parents=True)
    (base / "library" / "rules" / "x.md").write_text("rule x")
    (base / "tracker" / "backlog" / "tasks").mkdir(parents=True)
    (base / "tracker" / "backlog" / "tasks" / "card.yaml").write_text("id: HATS-1\n")
    (base / "sessions" / "runs").mkdir(parents=True)
    (base / "sessions" / "runs" / "log.txt").write_text("session log")
    (base / "STATE.md").write_text("# State\n")

    return project


def _read_gitignore(project: Path) -> list[str]:
    p = project / ".gitignore"
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text().splitlines() if ln.strip()]


def test_relocate_happy_path(tmp_path):
    project = _make_project(tmp_path)
    (project / ".gitignore").write_text(".agent/ai-hats/\n")

    asm = Assembler(project)
    result = asm.relocate(".foo")

    assert result.changed
    assert result.old == ".agent/ai-hats"
    assert result.new == ".foo"
    assert set(result.moved) == {"library", "tracker", "sessions", "STATE.md"}

    # Files at new location
    assert (project / ".foo" / "library" / "rules" / "x.md").read_text() == "rule x"
    assert (project / ".foo" / "tracker" / "backlog" / "tasks" / "card.yaml").exists()
    assert (project / ".foo" / "sessions" / "runs" / "log.txt").exists()
    assert (project / ".foo" / "STATE.md").exists()

    # Old dir gone (empty after the move)
    assert not (project / ".agent" / "ai-hats").exists()

    # yaml updated
    cfg = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    assert cfg.ai_hats_dir == ".foo"

    # .gitignore swapped
    assert ".foo/" in _read_gitignore(project)
    assert ".agent/ai-hats/" not in _read_gitignore(project)
    assert result.gitignore_updated


def test_relocate_noop_same_dir(tmp_path):
    project = _make_project(tmp_path, ai_hats_dir=".agent/ai-hats")
    asm = Assembler(project)
    result = asm.relocate(".agent/ai-hats")

    assert not result.changed
    assert result.old == result.new == ".agent/ai-hats"
    # Untouched
    assert (project / ".agent" / "ai-hats" / "library" / "rules" / "x.md").exists()


def test_relocate_destination_collision_raises(tmp_path):
    project = _make_project(tmp_path)
    # Pre-create a colliding library/ at destination
    (project / ".foo" / "library").mkdir(parents=True)
    (project / ".foo" / "library" / "stale.md").write_text("stale")

    asm = Assembler(project)
    with pytest.raises(ValueError, match="destination collision"):
        asm.relocate(".foo")

    # yaml NOT updated on failure
    cfg = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    assert cfg.ai_hats_dir == ".agent/ai-hats"


def test_relocate_idempotent_partial_recovery(tmp_path):
    """Simulate a previous run that moved library/ but crashed before yaml update."""
    project = _make_project(tmp_path)

    # Pre-move library/ to the new location, leave the rest at old.
    (project / ".foo").mkdir()
    import shutil as _sh
    _sh.move(str(project / ".agent" / "ai-hats" / "library"), str(project / ".foo" / "library"))

    asm = Assembler(project)
    result = asm.relocate(".foo")

    assert result.changed
    # library/ was already at destination — not in `moved`
    assert "library" not in result.moved
    assert set(result.moved) == {"tracker", "sessions", "STATE.md"}

    # All files now at new location
    assert (project / ".foo" / "library" / "rules" / "x.md").exists()
    assert (project / ".foo" / "tracker" / "backlog" / "tasks" / "card.yaml").exists()
    assert (project / ".foo" / "sessions" / "runs" / "log.txt").exists()


def test_relocate_managed_venv_is_removed(tmp_path):
    project = _make_project(tmp_path, venv_path=None)
    # Fake managed venv
    venv = project / ".agent" / "ai-hats" / ".venv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

    asm = Assembler(project)
    result = asm.relocate(".foo")

    assert result.venv_removed
    # Old venv gone
    assert not venv.exists()
    # New venv NOT recreated by relocate (launcher does that)
    assert not (project / ".foo" / ".venv").exists()


def test_relocate_external_venv_untouched(tmp_path):
    external = tmp_path / "my-venvs" / "proj"
    external.mkdir(parents=True)
    (external / "pyvenv.cfg").write_text("home = /usr/bin\n")

    project = _make_project(tmp_path, venv_path=str(external))
    # Also drop a stale managed venv inside old dir — should NOT be removed
    stale = project / ".agent" / "ai-hats" / ".venv"
    stale.mkdir(parents=True)
    (stale / "marker").write_text("x")

    asm = Assembler(project)
    result = asm.relocate(".foo")

    assert not result.venv_removed
    # External venv untouched
    assert external.exists()
    assert (external / "pyvenv.cfg").exists()
    # The stale managed-shaped venv stays where it was (relocate only deletes
    # when venv_path is None — user-managed venv_path means we don't presume
    # to touch anything at the old location's .venv).
    # It also wasn't part of _RELOCATE_ENTRIES, so it stayed at old path.
    assert stale.exists()


def test_relocate_unmanaged_gitignore_warns_via_result(tmp_path):
    project = _make_project(tmp_path, manage_gitignore=False)
    (project / ".gitignore").write_text(".agent/ai-hats/\n# user-owned\n")

    asm = Assembler(project)
    result = asm.relocate(".foo")

    assert result.changed
    assert not result.gitignore_updated
    # .gitignore untouched
    assert _read_gitignore(project) == [".agent/ai-hats/", "# user-owned"]


def test_relocate_no_gitignore_file_creates_it_when_managed(tmp_path):
    project = _make_project(tmp_path, manage_gitignore=True)
    # No .gitignore at all
    asm = Assembler(project)
    result = asm.relocate(".foo")

    assert result.gitignore_updated
    assert _read_gitignore(project) == [".foo/"]


def test_relocate_invalid_new_dir(tmp_path):
    project = _make_project(tmp_path)
    asm = Assembler(project)
    with pytest.raises(ValueError):
        asm.relocate("../escape")
    with pytest.raises(ValueError):
        asm.relocate("")
