"""Litmus role-swap tests for the canonical→publish layout (HATS-286).

3-step swap (A → B → A) verifies that:
- ./CLAUDE.md is byte-stable across role changes (user-owned scaffold).
- .claude/CLAUDE.md aggregator and .agent/ai-hats/role.md change with the
  active role and restore exactly when the role is set back.
- Bump after a swap is byte-stable (idempotency end-to-end).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler

# HATS-469: ``Assembler.bump()`` removed; use the test pipeline helper.
from tests._assembler_helpers import bump_pipeline


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
    (project / "ai-hats.yaml").write_text("schema_version: 3\nprovider: claude\n")
    return project


def test_role_swap_keeps_claude_md_byte_stable(project_two_roles: Path) -> None:
    asm = Assembler(project_two_roles)
    asm.init(provider="claude")
    asm.set_role("role-a", provider_name="claude")
    md5_step1 = md5(project_two_roles / "CLAUDE.md")

    asm.set_role("role-b", provider_name="claude")
    assert md5(project_two_roles / "CLAUDE.md") == md5_step1

    asm.set_role("role-a", provider_name="claude")
    assert md5(project_two_roles / "CLAUDE.md") == md5_step1


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
    profile = ProjectConfig.from_yaml(project_two_roles / "ai-hats.yaml")
    assert profile.active_role == "role-a"
    assert "Role A injection." in asm.composer.compose("role-a").role_injection

    asm.set_role("role-b", provider_name="claude")
    profile = ProjectConfig.from_yaml(project_two_roles / "ai-hats.yaml")
    assert profile.active_role == "role-b"
    assert "Role B injection." in asm.composer.compose("role-b").role_injection

    asm.set_role("role-a", provider_name="claude")
    profile = ProjectConfig.from_yaml(project_two_roles / "ai-hats.yaml")
    assert profile.active_role == "role-a"


def test_bump_after_role_swap_is_byte_stable(project_two_roles: Path) -> None:
    """HATS-294: only CLAUDE.md scaffold and imports.md (user-rules aggregator)
    survive on disk. Both must be byte-stable across bump.
    """
    asm = Assembler(project_two_roles)
    asm.init(provider="claude")
    asm.set_role("role-a", provider_name="claude")

    snapshot = {
        "claude_md": md5(project_two_roles / "CLAUDE.md"),
        "aggregator": md5(project_two_roles / ".agent" / "ai-hats" / "imports.md"),
    }

    bump_pipeline(asm)

    assert md5(project_two_roles / "CLAUDE.md") == snapshot["claude_md"]
    assert (
        md5(project_two_roles / ".agent" / "ai-hats" / "imports.md")
        == snapshot["aggregator"]
    )


def test_set_role_gemini_still_inline(tmp_path: Path) -> None:
    """Gemini path is unchanged — update_system_prompt still runs (no scaffold)."""
    project = tmp_path / "proj"
    project.mkdir()
    lib = project / "libraries"
    role_dir = lib / "roles" / "r1"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: r1\npriorities:\n  - Reliability\ninjection: |\n  Gemini role text.\n"
    )
    (project / "ai-hats.yaml").write_text("schema_version: 3\nprovider: gemini\n")

    Assembler(project).set_role("r1", provider_name="gemini")
    body = (project / "GEMINI.md").read_text()
    assert "Gemini role text." in body
    assert "<!-- AI-HATS:START -->" in body  # legacy markers, used by Gemini path


def test_set_role_claude_skips_inline_update(project_two_roles: Path) -> None:
    """./CLAUDE.md after set_role is exactly the scaffold — no inline content."""
    asm = Assembler(project_two_roles)
    asm.init(provider="claude")
    asm.set_role("role-a", provider_name="claude")

    body = (project_two_roles / "CLAUDE.md").read_text()
    # Scaffold only — no inline role content, no uppercase markers.
    assert "Role A injection." not in body
    assert "<!-- AI-HATS:START -->" not in body
    assert "@./.agent/ai-hats/imports.md" in body
