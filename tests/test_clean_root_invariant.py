"""Tests for Clean-Root Invariant and legacy root mirror sweeping (HATS-1237).

Verifies:
1. All legacy root materialization subdirectories (.agy/skills, .agy/rules, .gemini/skills,
   .gemini/rules, .cline/skills, .cline/plugins, .cline/rules, .clinerules, .agents) are swept
   upon session startup / init / sweeper.
2. Empty legacy parent directories (.agy, .gemini, .cline) are automatically removed.
3. User-owned files inside parent directories (.agy/user_file) are preserved.
4. Symlinked legacy directories are swept safely.
5. Sweeper ProcSurface handles directory markers (.agent/ai-hats).
6. Assembler.init and set_role trigger legacy sweeps.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ai_hats_core import CompositionResult
from ai_hats.plugin_dir import drop_legacy_root_skills_mirrors
from ai_hats.sweeper import ProcSurface, sweep_unclaimed


def test_drop_legacy_root_skills_mirrors_comprehensive(tmp_path: Path) -> None:
    """Corner case 1: Sweeps all legacy subdirectories (.agy/skills, .agy/rules, etc.)."""
    targets = (
        tmp_path / ".agy" / "skills",
        tmp_path / ".agy" / "rules",
        tmp_path / ".gemini" / "skills",
        tmp_path / ".gemini" / "rules",
        tmp_path / ".cline" / "skills",
        tmp_path / ".cline" / "plugins",
        tmp_path / ".cline" / "rules",
        tmp_path / ".clinerules",
        tmp_path / ".agents",
    )
    for t in targets:
        if t.suffix or t.name.startswith(".clinerules"):
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text("file content")
        else:
            t.mkdir(parents=True, exist_ok=True)
            (t / "dummy.txt").write_text("content")

    removed = drop_legacy_root_skills_mirrors(tmp_path)

    assert sorted(removed) == [
        ".agents",
        ".agy/rules",
        ".agy/skills",
        ".cline/plugins",
        ".cline/rules",
        ".cline/skills",
        ".clinerules",
        ".gemini/rules",
        ".gemini/skills",
    ]
    for t in targets:
        assert not t.exists()

    # Empty parent dirs removed
    assert not (tmp_path / ".agy").exists()
    assert not (tmp_path / ".gemini").exists()
    assert not (tmp_path / ".cline").exists()


def test_drop_legacy_root_skills_mirrors_empty_parent_dirs(tmp_path: Path) -> None:
    """Corner case 2: Sweeps empty legacy parent directories even if no skills subdirs exist."""
    (tmp_path / ".agy").mkdir()
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".cline").mkdir()

    removed = drop_legacy_root_skills_mirrors(tmp_path)

    assert removed == []
    assert not (tmp_path / ".agy").exists()
    assert not (tmp_path / ".gemini").exists()
    assert not (tmp_path / ".cline").exists()


def test_drop_legacy_root_skills_mirrors_preserves_user_files(tmp_path: Path) -> None:
    """Corner case 3: Preserves parent directory if it contains user-owned non-framework files."""
    agy_skills = tmp_path / ".agy" / "skills"
    agy_skills.mkdir(parents=True)
    (agy_skills / "dummy.txt").write_text("legacy skill")

    user_file = tmp_path / ".agy" / "user_custom.json"
    user_file.write_text('{"key": "value"}')

    removed = drop_legacy_root_skills_mirrors(tmp_path)

    assert ".agy/skills" in removed
    assert not agy_skills.exists()
    # Parent .agy directory and user file MUST be preserved
    assert (tmp_path / ".agy").exists()
    assert user_file.exists()
    assert user_file.read_text() == '{"key": "value"}'


def test_drop_legacy_root_skills_mirrors_handles_symlinks(tmp_path: Path) -> None:
    """Corner case 4: Handles symlinks gracefully."""
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    (real_dir / "file.txt").write_text("test")

    symlink_dir = tmp_path / ".agents"
    symlink_dir.symlink_to(real_dir, target_is_directory=True)

    removed = drop_legacy_root_skills_mirrors(tmp_path)

    assert ".agents" in removed
    assert not symlink_dir.exists()
    # Target directory remains intact
    assert real_dir.exists()


def test_sweeper_proc_surface_handles_directory_marker(tmp_path: Path) -> None:
    """Corner case 5: ProcSurface in sweeper.py handles directory markers (.agent/ai-hats)."""
    marker_dir = tmp_path / ".agent" / "ai-hats"
    marker_dir.mkdir(parents=True)

    agy_skills = tmp_path / ".agy" / "skills"
    agy_skills.mkdir(parents=True)
    (agy_skills / "skill.py").write_text("print('hello')")

    proc_surface = ProcSurface(
        owner_key="root-skills-export",
        marker_relpath=".agent/ai-hats",
        proc=drop_legacy_root_skills_mirrors,
    )

    reports = sweep_unclaimed(tmp_path, surfaces=(proc_surface,))
    assert len(reports) == 1
    assert not agy_skills.exists()


def test_assembler_cleanup_on_init_and_set_role(tmp_path: Path) -> None:
    """Corner case 6: Assembler.init and set_role trigger legacy sweeps."""
    from ai_hats.assembler import Assembler

    agy_skills = tmp_path / ".agy" / "skills"
    agy_skills.mkdir(parents=True)
    (agy_skills / "skill.py").write_text("print('hello')")

    mock_res = CompositionResult(
        name="maintainer",
        injections=[],
        rules=[],
        skills=[],
        user_rules=[],
        priorities=[],
    )
    with (
        patch.object(Assembler, "_refresh"),
        patch("ai_hats.materialize.compose_for_role", return_value=mock_res),
    ):
        asm = Assembler(tmp_path)
        asm.set_role("maintainer")

    assert not agy_skills.exists()
    assert not (tmp_path / ".agy").exists()
