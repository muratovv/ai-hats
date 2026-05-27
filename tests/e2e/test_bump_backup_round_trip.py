"""E2E: pre-bump backup tarball round-trip (HATS-549 Phase 1).

Validates the load-bearing safety net:

  - Before any destructive migration step runs, ``ai-hats self update``
    snapshots the project's ai-hats-managed surface to a ``.tar.gz``
    under :env:`AI_HATS_BUMP_BACKUP_DIR`.
  - The path is printed to stderr with a ``[ai-hats] migration backup``
    banner and a ``Recovery: tar -xzf`` one-liner.
  - The tarball's payload, when extracted over the post-bump tree,
    restores byte-identical pre-bump state for the scoped paths.

Per ``dev_rule_e2e_gate``: real ``ai-hats`` binary, real subprocess.
Fail-under-revert against commit ``fac79c0`` / ``0eab1c5`` (Phase 1).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

import pytest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_proxmox_shape(project_path: Path) -> None:
    """v3-shape project: yaml + user-owned legacy hook + settings.json."""
    (project_path / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "default_role: assistant\n"
        "active_role: assistant\n"
        "task_prefix: HATS\n"
    )
    hooks = project_path / ".agent" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "guard.py").write_text("#!/usr/bin/env python3\nprint('guard')\n")
    (hooks / "guard.py").chmod(0o755)
    claude = project_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": "$CLAUDE_PROJECT_DIR/.agent/hooks/guard.py",
            }],
        }]},
    }, indent=2) + "\n")
    # Git init so the healer's git-clean gate has a baseline.
    subprocess.run(
        ["git", "init", "-q"], cwd=str(project_path), check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.t"],
        cwd=str(project_path), check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=str(project_path), check=True,
    )
    subprocess.run(
        ["git", "add", "-A"], cwd=str(project_path), check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=str(project_path), check=True,
    )


@pytest.mark.integration
def test_bump_produces_backup_with_recovery_banner(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """AC1: backup tarball written BEFORE migration runs, path
    printed to stderr with a recovery one-liner."""
    _seed_proxmox_shape(tmp_venv_project.path)
    backup_dir = tmp_path / "backups"

    res = tmp_venv_project.run(
        "self", "update",
        timeout=180,
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(backup_dir)},
    )

    # Banner present (regardless of bump exit code — backup runs FIRST).
    assert "[ai-hats] migration backup →" in res.stderr, (
        f"backup banner missing from stderr:\n{res.stderr[-500:]}"
    )
    assert "Recovery: tar -xzf" in res.stderr, (
        f"recovery hint missing:\n{res.stderr[-500:]}"
    )
    # Tarball materialised on disk under our isolated backup dir.
    tarballs = list(backup_dir.glob("*.tar.gz"))
    assert len(tarballs) == 1, (
        f"expected exactly one tarball, found: {tarballs}"
    )


@pytest.mark.integration
def test_backup_captures_scoped_surface(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """AC2 part 1: tarball contains the declared scope
    (.agent/, ai-hats.yaml, .claude/settings.json, CLAUDE.md, ...)."""
    _seed_proxmox_shape(tmp_venv_project.path)
    (tmp_venv_project.path / "CLAUDE.md").write_text("# Project\n")
    backup_dir = tmp_path / "backups"

    tmp_venv_project.run(
        "self", "update",
        timeout=180,
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(backup_dir)},
    )

    tarball = next(backup_dir.glob("*.tar.gz"))
    with tarfile.open(tarball, "r:gz") as tar:
        names = set(tar.getnames())

    # The PRE-bump state was captured — legacy .agent/hooks/guard.py
    # must be in the tarball even though it's gone from the working
    # tree after bump.
    assert "ai-hats.yaml" in names
    assert ".claude/settings.json" in names
    assert "CLAUDE.md" in names
    # Pre-bump .agent/hooks/ entries are inside the captured .agent/
    # subtree (tarfile reports the inner files when recursive=True).
    has_legacy_hook = any(
        n == ".agent/hooks/guard.py" or n.endswith("/guard.py")
        for n in names
    )
    assert has_legacy_hook, (
        f"pre-bump legacy hook missing from backup; got: {sorted(names)[:30]}"
    )


@pytest.mark.integration
def test_backup_round_trip_restores_state_byte_for_byte(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """AC2 part 2: ``tar -xzf <backup> -C <project>`` restores
    byte-identical state for scoped paths."""
    _seed_proxmox_shape(tmp_venv_project.path)
    backup_dir = tmp_path / "backups"

    # Snapshot pre-bump hashes for the load-bearing files.
    pre = {
        "ai-hats.yaml": _sha256(tmp_venv_project.path / "ai-hats.yaml"),
        ".claude/settings.json": _sha256(
            tmp_venv_project.path / ".claude" / "settings.json"
        ),
        ".agent/hooks/guard.py": _sha256(
            tmp_venv_project.path / ".agent" / "hooks" / "guard.py"
        ),
    }

    tmp_venv_project.run(
        "self", "update",
        timeout=180,
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(backup_dir)},
    )

    tarball = next(backup_dir.glob("*.tar.gz"))

    # Restore over the post-bump tree.
    subprocess.run(
        ["tar", "-xzf", str(tarball), "-C", str(tmp_venv_project.path)],
        check=True,
    )

    # Post-restore hashes match pre-bump for every scoped file.
    for rel, expected in pre.items():
        actual = _sha256(tmp_venv_project.path / rel)
        assert actual == expected, (
            f"{rel} not byte-identical after restore: "
            f"expected sha256 {expected}, got {actual}"
        )


@pytest.mark.integration
def test_backup_excludes_venv_and_pycache(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """Tarball must NOT include regenerable derived state. Without
    exclusions the framework venv alone inflates the tarball ~100×."""
    _seed_proxmox_shape(tmp_venv_project.path)
    # The tmp_venv_project fixture's shared venv is reached via
    # AI_HATS_VENV — it doesn't live under <project>/.agent/. To
    # exercise the EXCLUSION codepath we plant a fake venv inside
    # .agent/ai-hats/.venv/ that the snapshot would otherwise capture.
    fake_venv = tmp_venv_project.path / ".agent" / "ai-hats" / ".venv" / "bin"
    fake_venv.mkdir(parents=True)
    (fake_venv / "python").write_text("#!/bin/sh\n")
    pycache = tmp_venv_project.path / ".agent" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "x.cpython-314.pyc").write_text("bytecode")
    backup_dir = tmp_path / "backups"

    tmp_venv_project.run(
        "self", "update",
        timeout=180,
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(backup_dir)},
    )

    tarball = next(backup_dir.glob("*.tar.gz"))
    with tarfile.open(tarball, "r:gz") as tar:
        names = set(tar.getnames())

    assert not any(".venv" in n.split("/") for n in names), (
        f".venv leaked into tarball: {[n for n in names if '.venv' in n][:5]}"
    )
    assert not any("__pycache__" in n.split("/") for n in names)
    assert not any(n.endswith(".pyc") for n in names)
