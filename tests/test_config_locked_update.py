"""HATS-526 (review extension): every ai-hats.yaml writer preserves concurrent edits.

Deterministic interleaved-marker pattern, no process races: load the config,
write a marker change to the yaml BEHIND the loaded object's back (a simulated
concurrent session), run the operation under test, assert BOTH the marker and
the operation's own fields survive. Whole-object saves of a stale in-memory
config fail these; ``locked_update`` re-reads under lock and applies a delta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.config.project import ProjectConfig, locked_update
from ai_hats.models import OverlayConfig


def _write_marker(config_path: Path) -> None:
    """Simulated concurrent session: adds a customization directly on disk."""
    cfg = ProjectConfig.from_yaml(config_path)
    cfg.customizations["marker-role"] = OverlayConfig.from_dict(
        {"add": {"traits": ["marker-trait"]}}
    )
    cfg.save(config_path)


def _assert_marker_alive(config_path: Path) -> None:
    on_disk = ProjectConfig.from_yaml(config_path)
    assert on_disk.customizations["marker-role"].add_traits == ["marker-trait"], (
        f"concurrent customization lost by a stale whole-object save:\n"
        f"{config_path.read_text()}"
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "project"
    proj.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(proj / "ai-hats.yaml")
    return proj


def test_locked_update_applies_delta_over_fresh_state(project: Path) -> None:
    config_path = project / "ai-hats.yaml"
    _write_marker(config_path)

    updated = locked_update(config_path, lambda c: setattr(c, "task_prefix", "ACME"))

    assert updated.task_prefix == "ACME"
    _assert_marker_alive(config_path)
    assert ProjectConfig.from_yaml(config_path).task_prefix == "ACME"


def test_save_config_refreshes_in_memory_and_keeps_marker(project: Path) -> None:
    asm = Assembler(project)
    _write_marker(asm.config_path)

    asm.save_config(task_prefix="ACME")

    _assert_marker_alive(asm.config_path)
    assert asm.project_config.task_prefix == "ACME"
    assert asm.project_config.customizations["marker-role"].add_traits == ["marker-trait"]


def test_init_preserves_concurrent_customization(project: Path) -> None:
    asm = Assembler(project)
    _write_marker(asm.config_path)

    asm.init(provider="gemini")

    on_disk = ProjectConfig.from_yaml(asm.config_path)
    assert on_disk.provider == "gemini"
    _assert_marker_alive(asm.config_path)


def test_persist_migration_step_preserves_concurrent_customization(
    project: Path,
) -> None:
    asm = Assembler(project)
    _write_marker(asm.config_path)

    asm.project_config.migration_step += 1
    asm._persist_migration_step(asm.project_config.migration_step)

    _assert_marker_alive(asm.config_path)
