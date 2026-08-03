"""Migration step 9 — drop the retired ``library/wt-hooks/`` dir (HATS-1269).

Worktree hooks spawn in place from their declaring skill now, so nothing writes
or sweeps that directory any more. Without this step every upgraded project
keeps a managed tree with no owner — ADR-0021 M8 (no second managed copy of a
hook script) and M9 (every materialized class has a retention policy).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.migrations import _m_drop_retired_wt_hooks
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def _project_with_residue(tmp_path: Path) -> Assembler:
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(provider="claude").save(project / PROJECT_CONFIG)
    wt = project / ".agent" / "ai-hats" / "library" / "wt-hooks"
    wt.mkdir(parents=True)
    (wt / ".manifest").write_text("# ai-hats managed — do not edit\ndrainer-drain.sh\n")
    (wt / "drainer-drain.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    return Assembler(project)


def _residue(asm: Assembler) -> Path:
    return asm.project_dir / ".agent" / "ai-hats" / "library" / "wt-hooks"


def test_retired_dir_is_removed(tmp_path: Path) -> None:
    asm = _project_with_residue(tmp_path)

    _m_drop_retired_wt_hooks(asm)

    assert not _residue(asm).exists()


def test_rerun_on_migrated_state_is_a_noop(tmp_path: Path) -> None:
    """The registry may replay a step under concurrent install-time refreshes."""
    asm = _project_with_residue(tmp_path)

    _m_drop_retired_wt_hooks(asm)
    _m_drop_retired_wt_hooks(asm)

    assert not _residue(asm).exists()
    assert (asm.project_dir / ".agent" / "ai-hats" / "library").is_dir()


def test_a_foreign_file_in_the_dir_is_left_alone(tmp_path: Path) -> None:
    """Only what the manifest claimed was ever ours — an unmanaged file next to
    it is somebody's, and this migration never guesses."""
    asm = _project_with_residue(tmp_path)
    stranger = _residue(asm) / "notes.txt"
    stranger.write_text("mine\n")

    _m_drop_retired_wt_hooks(asm)

    assert stranger.exists()
    assert not (_residue(asm) / "drainer-drain.sh").exists()
    assert not (_residue(asm) / ".manifest").exists()
