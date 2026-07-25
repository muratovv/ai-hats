"""Litmus role-swap tests for the canonical→publish layout (HATS-286).

3-step swap (A → B → A) verifies that:
- no root CLAUDE.md is ever created, whatever the role (HATS-1170).
- .agent/ai-hats/imports.md restores exactly when the role is set back.
- Bump after a swap is byte-stable (idempotency end-to-end).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler

# HATS-469: ``Assembler.bump()`` removed; use the test pipeline helper.
from tests._assembler_helpers import bump_pipeline
from ai_hats.paths import PROJECT_CONFIG


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


@pytest.fixture
def project_two_roles(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    lib = project / "libraries"

    for name, text in [
        ("role-a", "Role A injection."),
        ("role-b", "Role B injection."),
    ]:
        d = lib / "roles" / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(
            f"name: {name}\npriorities:\n  - Reliability\ninjection: |\n  {text}\n"
        )
    (project / PROJECT_CONFIG).write_text("schema_version: 3\nprovider: claude\n")
    return project


def test_role_swap_never_creates_root_claude_md(project_two_roles: Path) -> None:
    """HATS-1170: no swap, in either direction, materializes a root CLAUDE.md."""
    asm = Assembler(project_two_roles)
    asm.init(provider="claude")
    asm.set_role("role-a", provider_name="claude")
    assert not (project_two_roles / "CLAUDE.md").exists()

    asm.set_role("role-b", provider_name="claude")
    assert not (project_two_roles / "CLAUDE.md").exists()

    asm.set_role("role-a", provider_name="claude")
    assert not (project_two_roles / "CLAUDE.md").exists()


def test_role_swap_tracks_active_role_in_profile(
    project_two_roles: Path,
) -> None:
    """HATS-294: role content lives in composition memory, not on disk.
    Role swap is reflected in ``project_config.active_role``; the composed
    result still carries the role-specific injection.
    """
    from ai_hats.models import ProjectConfig

    asm = Assembler(project_two_roles)
    asm.init(provider="claude")
    asm.set_role("role-a", provider_name="claude")
    profile = ProjectConfig.from_yaml(project_two_roles / PROJECT_CONFIG)
    assert profile.active_role == "role-a"
    assert "Role A injection." in asm.composer.compose("role-a").role_injection

    asm.set_role("role-b", provider_name="claude")
    profile = ProjectConfig.from_yaml(project_two_roles / PROJECT_CONFIG)
    assert profile.active_role == "role-b"
    assert "Role B injection." in asm.composer.compose("role-b").role_injection

    asm.set_role("role-a", provider_name="claude")
    profile = ProjectConfig.from_yaml(project_two_roles / PROJECT_CONFIG)
    assert profile.active_role == "role-a"


def test_bump_after_role_swap_is_byte_stable(project_two_roles: Path) -> None:
    """HATS-1170: imports.md (user-rules aggregator) is the only survivor on
    disk, and it must be byte-stable across bump.
    """
    asm = Assembler(project_two_roles)
    asm.init(provider="claude")
    asm.set_role("role-a", provider_name="claude")

    aggregator = project_two_roles / ".agent" / "ai-hats" / "imports.md"
    before = md5(aggregator)

    bump_pipeline(asm)

    assert md5(aggregator) == before
    assert not (project_two_roles / "CLAUDE.md").exists()


def test_set_role_agy_still_inline(tmp_path: Path) -> None:
    """Agy path is unchanged — update_system_prompt still runs (no scaffold)."""
    project = tmp_path / "proj"
    project.mkdir()
    lib = project / "libraries"
    role_dir = lib / "roles" / "r1"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: r1\npriorities:\n  - Reliability\ninjection: |\n  Agy role text.\n"
    )
    (project / PROJECT_CONFIG).write_text("schema_version: 3\nprovider: agy\n")

    Assembler(project).set_role("r1", provider_name="agy")
    body = (project / "GEMINI.md").read_text()
    assert "Agy role text." in body
    assert "<!-- AI-HATS:START -->" in body  # legacy markers, used by Agy path


def test_set_role_claude_skips_inline_update(project_two_roles: Path) -> None:
    """Claude's update_system_prompt is a no-op — the role never hits the root."""
    asm = Assembler(project_two_roles)
    asm.init(provider="claude")
    asm.set_role("role-a", provider_name="claude")

    assert not (project_two_roles / "CLAUDE.md").exists()
    assert "Role A injection." not in (
        project_two_roles / ".agent" / "ai-hats" / "imports.md"
    ).read_text()
