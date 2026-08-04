"""Migration step 10 — drop the retired ``library/hooks/`` dir (HATS-1480).

Claude's runtime hooks resolve inside the session tree now (HATS-1268), so
nothing writes or sweeps that directory any more. Without this step every
upgraded project keeps a managed tree with no owner — ADR-0021 M8 (no second
managed copy of a hook script) and M9 (every materialized class has a retention
policy). Mirrors ``test_migration_wt_hooks_residue.py``, the step-9 precedent.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.migrations import _m_drop_retired_runtime_hooks
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def _project_with_residue(tmp_path: Path) -> Assembler:
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(provider="claude").save(project / PROJECT_CONFIG)
    hooks = project / ".agent" / "ai-hats" / "library" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / ".manifest").write_text(
        "# ai-hats managed — do not edit\nbypass_journal.sh\nsafety-guard-safety_gate.py\n"
    )
    (hooks / "bypass_journal.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (hooks / "safety-guard-safety_gate.py").write_text("#!/usr/bin/env python3\n")
    return Assembler(project)


def _residue(asm: Assembler) -> Path:
    return asm.project_dir / ".agent" / "ai-hats" / "library" / "hooks"


def test_retired_dir_is_removed(tmp_path: Path) -> None:
    asm = _project_with_residue(tmp_path)

    _m_drop_retired_runtime_hooks(asm)

    assert not _residue(asm).exists()


def test_rerun_on_migrated_state_is_a_noop(tmp_path: Path) -> None:
    """The registry may replay a step under concurrent install-time refreshes."""
    asm = _project_with_residue(tmp_path)

    _m_drop_retired_runtime_hooks(asm)
    _m_drop_retired_runtime_hooks(asm)

    assert not _residue(asm).exists()
    assert (asm.project_dir / ".agent" / "ai-hats" / "library").is_dir()


def test_step_on_a_project_that_never_had_the_dir(tmp_path: Path) -> None:
    """A fresh install post-HATS-1480 has no such dir — the step must not create one."""
    project = tmp_path / "fresh"
    project.mkdir()
    ProjectConfig(provider="claude").save(project / PROJECT_CONFIG)
    asm = Assembler(project)

    _m_drop_retired_runtime_hooks(asm)

    assert not _residue(asm).exists()


def test_a_foreign_file_in_the_dir_is_left_alone(tmp_path: Path) -> None:
    """Only what the manifest claimed was ever ours — an unmanaged file next to
    it is somebody's, and this migration never guesses."""
    asm = _project_with_residue(tmp_path)
    stranger = _residue(asm) / "notes.txt"
    stranger.write_text("mine\n")

    _m_drop_retired_runtime_hooks(asm)

    assert stranger.exists()
    assert not (_residue(asm) / "bypass_journal.sh").exists()
    assert not (_residue(asm) / "safety-guard-safety_gate.py").exists()
    assert not (_residue(asm) / ".manifest").exists()
