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

HATS-592 (gate-wall perf): the four checks below are all facets of the
SAME pre-bump backup. Each used to rebuild the (proxmox seed →
``self update``) cycle independently — 4× a ~13-21s bump dominated the
file's wall (the binding floor under ``--dist=loadgroup``, where the
whole file pins to one worker). They now share ONE module-scoped bump
(:func:`bumped`); each test is a cheap assertion on its tarball /
post-bump tree. The only mutating check extracts into a fresh dir, never
the shared project, so the module fixture stays immutable across tests.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

# Load-bearing files whose pre-bump bytes the round-trip check restores.
_ROUND_TRIP_FILES = ("ai-hats.yaml", ".claude/settings.json", ".agent/hooks/guard.py")


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


@pytest.fixture(scope="module")
def bumped(tmp_path_factory, _shared_launcher_venv, repo_root: Path):
    """One shared ``self update`` run — the four checks are facets of it.

    Builds a SUPERSET pre-bump seed so a single bump satisfies every
    assertion in this module:

    * proxmox shape (``ai-hats.yaml`` + legacy ``.agent/hooks/guard.py`` +
      ``.claude/settings.json``) — banner / round-trip / scoped-surface;
    * ``CLAUDE.md`` — scoped-surface capture;
    * planted ``.agent/ai-hats/.venv`` + ``.agent/__pycache__`` — derived
      state that the exclusion check expects to be filtered out.

    The extra files are added AFTER the seed commit (dirty tree) — matching
    the original per-test ordering. The backup runs FIRST, before any
    migration step, so it is produced regardless of the bump's exit code or
    a dirty tree.

    Reuses the session-shared venv (``_shared_launcher_venv``) — no new venv
    build. Module scope is coherent under ``--dist=loadgroup``: the whole
    file is one xdist group → one worker, so the fixture runs exactly once.

    Returns a namespace: ``project_path``, ``backup_dir``, ``res``
    (the ``RunResult``), ``pre_hashes`` (pre-bump sha256 of the round-trip
    files).
    """
    from _helpers.project import Project
    from _helpers.repo_src import build_src

    launcher, shared_venv = _shared_launcher_venv
    project_path = tmp_path_factory.mktemp("bump-backup") / "project"
    project_path.mkdir()
    project = Project(
        path=project_path,
        ai_hats_binary=launcher,
        env={
            # HATS-589: per-worker private build source (no-op on serial).
            "AI_HATS_REPO_URL": str(build_src(repo_root)),
            "AI_HATS_VENV": str(shared_venv),
        },
    )

    _seed_proxmox_shape(project_path)
    (project_path / "CLAUDE.md").write_text("# Project\n")
    # Plant derived state the snapshot must EXCLUDE. The shared venv lives
    # outside <project>/.agent/ (reached via AI_HATS_VENV); this fake one
    # exercises the exclusion codepath.
    fake_venv = project_path / ".agent" / "ai-hats" / ".venv" / "bin"
    fake_venv.mkdir(parents=True)
    (fake_venv / "python").write_text("#!/bin/sh\n")
    pycache = project_path / ".agent" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "x.cpython-314.pyc").write_text("bytecode")

    # Snapshot pre-bump hashes BEFORE the bump rewrites the surface.
    pre_hashes = {rel: _sha256(project_path / rel) for rel in _ROUND_TRIP_FILES}

    backup_dir = tmp_path_factory.mktemp("bump-backup-out")
    res = project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(backup_dir)},
    )
    return SimpleNamespace(
        project_path=project_path,
        backup_dir=backup_dir,
        res=res,
        pre_hashes=pre_hashes,
    )


@pytest.mark.integration
def test_bump_produces_backup_with_recovery_banner(bumped) -> None:
    """AC1: backup tarball written BEFORE migration runs, path
    printed to stderr with a recovery one-liner."""
    # Banner present (regardless of bump exit code — backup runs FIRST).
    assert "[ai-hats] migration backup →" in bumped.res.stderr, (
        f"backup banner missing from stderr:\n{bumped.res.stderr[-500:]}"
    )
    assert "Recovery: tar -xzf" in bumped.res.stderr, (
        f"recovery hint missing:\n{bumped.res.stderr[-500:]}"
    )
    # Tarball materialised on disk under our isolated backup dir.
    tarballs = list(bumped.backup_dir.glob("*.tar.gz"))
    assert len(tarballs) == 1, (
        f"expected exactly one tarball, found: {tarballs}"
    )


@pytest.mark.integration
def test_backup_captures_scoped_surface(bumped) -> None:
    """AC2 part 1: tarball contains the declared scope
    (.agent/, ai-hats.yaml, .claude/settings.json, CLAUDE.md, ...)."""
    tarball = next(bumped.backup_dir.glob("*.tar.gz"))
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
@pytest.mark.quarantine(
    reason="flaky under the -n8 pre-push gate: intermittent StopIteration on "
    "`next(bumped.backup_dir.glob('*.tar.gz'))` — empty backup_dir (module-scoped "
    "shared-venv bump produced no tarball under contention). Passed solo / gate "
    "attempt-1, failed a later identical-code run. Quarantined HATS-676; "
    "de-flake follow-up HATS-677."
)
def test_backup_round_trip_restores_state_byte_for_byte(
    bumped, tmp_path: Path,
) -> None:
    """AC2 part 2: ``tar -xzf <backup>`` restores byte-identical state
    for scoped paths."""
    tarball = next(bumped.backup_dir.glob("*.tar.gz"))

    # Extract into a FRESH dir — never the shared module-scoped project —
    # so the other assertions still see the post-bump tree untouched.
    restore = tmp_path / "restore"
    restore.mkdir()
    subprocess.run(
        ["tar", "-xzf", str(tarball), "-C", str(restore)],
        check=True,
    )

    # Post-restore hashes match pre-bump for every scoped file.
    for rel, expected in bumped.pre_hashes.items():
        actual = _sha256(restore / rel)
        assert actual == expected, (
            f"{rel} not byte-identical after restore: "
            f"expected sha256 {expected}, got {actual}"
        )


@pytest.mark.integration
def test_backup_excludes_venv_and_pycache(bumped) -> None:
    """Tarball must NOT include regenerable derived state. Without
    exclusions the framework venv alone inflates the tarball ~100×."""
    tarball = next(bumped.backup_dir.glob("*.tar.gz"))
    with tarfile.open(tarball, "r:gz") as tar:
        names = set(tar.getnames())

    assert not any(".venv" in n.split("/") for n in names), (
        f".venv leaked into tarball: {[n for n in names if '.venv' in n][:5]}"
    )
    assert not any("__pycache__" in n.split("/") for n in names)
    assert not any(n.endswith(".pyc") for n in names)
