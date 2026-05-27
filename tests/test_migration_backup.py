"""Unit tests for migration_backup (HATS-549 Phase 1).

Covers ``snapshot_pre_bump`` in-process behaviour: scope inclusion /
exclusion, env override (custom dir + hard-disable sentinel), retention
sweep, BackupError on unwritable destinations, round-trip restore.

E2E coverage (real ``ai-hats self update`` subprocess writing a real
tarball under a real ``/tmp/``) lives in
``tests/e2e/test_bump_backup_round_trip.py``.
"""
from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_hats.migration_backup import (
    BACKUP_SCOPE_PATHS,
    ENV_BACKUP_DIR,
    HARD_DISABLE_SENTINEL,
    MAX_RETENTION,
    BackupError,
    _project_slug,
    snapshot_pre_bump,
)


# ---------- Helpers ----------


def _seed_project(project_dir: Path) -> None:
    """Build a minimal post-bump-shape project: yaml, .agent/, settings."""
    (project_dir / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    agent = project_dir / ".agent" / "ai-hats" / "library" / "hooks"
    agent.mkdir(parents=True)
    (agent / "pre_bash_shared_state_guard.sh").write_text("#!/bin/sh\nexit 0\n")
    (agent / ".manifest").write_text("pre_bash_shared_state_guard.sh\n")
    claude = project_dir / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text('{"hooks": {}}\n')
    (project_dir / "CLAUDE.md").write_text("# system prompt\n")
    (project_dir / ".gitignore").write_text(".agent/ai-hats/\n")


def _isolate_backup_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point AI_HATS_BUMP_BACKUP_DIR at a test-controlled dir."""
    backup_root = tmp_path / "backup-root"
    monkeypatch.setenv(ENV_BACKUP_DIR, str(backup_root))
    return backup_root


# ---------- Scope (what the tarball captures) ----------


def test_snapshot_captures_all_declared_scope_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each path listed in BACKUP_SCOPE_PATHS that exists on disk
    must appear in the tarball with its project-relative arcname."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    backup_root = _isolate_backup_dir(monkeypatch, tmp_path)

    snap = snapshot_pre_bump(project)

    assert snap is not None
    assert snap.exists()
    assert snap.parent == backup_root
    with tarfile.open(snap, "r:gz") as tar:
        names = set(tar.getnames())
    # Files we seeded must be present
    assert "ai-hats.yaml" in names
    assert "CLAUDE.md" in names
    assert ".gitignore" in names
    assert ".agent" in names
    assert ".claude/settings.json" in names
    # Recursive content
    assert ".agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh" in names


def test_snapshot_skips_paths_not_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing scope paths (e.g. no GEMINI.md on claude projects, no
    .githooks/) are silently skipped — not an error."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text("schema_version: 4\n")
    _isolate_backup_dir(monkeypatch, tmp_path)

    snap = snapshot_pre_bump(project)

    assert snap is not None
    with tarfile.open(snap, "r:gz") as tar:
        names = set(tar.getnames())
    assert names == {"ai-hats.yaml"}


def test_snapshot_works_on_empty_greenfield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No yaml, no .agent/, nothing — still produces a valid (empty)
    tarball. The marker-of-attempt matters for audit."""
    project = tmp_path / "proj"
    project.mkdir()
    _isolate_backup_dir(monkeypatch, tmp_path)

    snap = snapshot_pre_bump(project)

    assert snap is not None
    assert snap.exists()
    with tarfile.open(snap, "r:gz") as tar:
        assert list(tar.getnames()) == []


def test_snapshot_preserves_file_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hook scripts ship chmod +x — the backup must preserve mode so
    restore is byte-AND-mode-identical."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text("schema_version: 4\n")
    hook = project / ".agent" / "ai-hats" / "library" / "hooks" / "x.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\n")
    hook.chmod(0o755)
    _isolate_backup_dir(monkeypatch, tmp_path)

    snap = snapshot_pre_bump(project)
    assert snap is not None

    with tarfile.open(snap, "r:gz") as tar:
        info = tar.getmember(".agent/ai-hats/library/hooks/x.sh")
        # 0o100755 = regular file with 0o755 perms.
        assert info.mode & 0o777 == 0o755


# ---------- Round-trip restore ----------


def test_snapshot_round_trip_restores_state_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of AC2: tar -xzf over the post-bump tree
    reproduces the pre-bump state byte-for-byte for scoped paths."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    _isolate_backup_dir(monkeypatch, tmp_path)
    original_settings = (project / ".claude" / "settings.json").read_bytes()
    original_yaml = (project / "ai-hats.yaml").read_bytes()

    snap = snapshot_pre_bump(project)
    assert snap is not None

    # Simulate a destructive bump: mutate the files.
    (project / ".claude" / "settings.json").write_text('{"DESTROYED": true}')
    (project / "ai-hats.yaml").write_text("DESTROYED")
    (project / ".agent" / "ai-hats" / "library" / "hooks" / "pre_bash_shared_state_guard.sh").unlink()

    # Recover via tarball.
    with tarfile.open(snap, "r:gz") as tar:
        tar.extractall(path=project, filter="data")

    assert (project / ".claude" / "settings.json").read_bytes() == original_settings
    assert (project / "ai-hats.yaml").read_bytes() == original_yaml
    assert (project / ".agent" / "ai-hats" / "library" / "hooks" / "pre_bash_shared_state_guard.sh").exists()


# ---------- Env overrides ----------


def test_hard_disable_sentinel_returns_none_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AI_HATS_BUMP_BACKUP_DIR=- → no tarball, WARN to stderr, no
    BackupError. The CI / sandbox escape hatch."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    monkeypatch.setenv(ENV_BACKUP_DIR, HARD_DISABLE_SENTINEL)
    # Make sure default tmp doesn't get polluted in case logic is wrong.
    sentinel_base = tmp_path / "should-not-be-created"
    monkeypatch.setenv("TMPDIR", str(sentinel_base))

    result = snapshot_pre_bump(project)

    assert result is None
    captured = capsys.readouterr()
    assert "DISABLED" in captured.err
    assert not sentinel_base.exists()


def test_custom_backup_dir_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI_HATS_BUMP_BACKUP_DIR=<path> redirects the base dir."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    custom = tmp_path / "custom-backup-dir"
    monkeypatch.setenv(ENV_BACKUP_DIR, str(custom))

    snap = snapshot_pre_bump(project)
    assert snap is not None
    assert snap.parent == custom


def test_unwritable_backup_dir_raises_backup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-only fs / permission failure → BackupError, NOT silent
    success. The contract is 'no migration without backup'."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    readonly_parent = tmp_path / "readonly"
    readonly_parent.mkdir()
    readonly_parent.chmod(0o500)  # r-x only — can't mkdir inside
    try:
        monkeypatch.setenv(ENV_BACKUP_DIR, str(readonly_parent / "nested"))
        with pytest.raises(BackupError):
            snapshot_pre_bump(project)
    finally:
        # Restore perms so cleanup works.
        readonly_parent.chmod(0o700)


# ---------- Retention ----------


def test_retention_keeps_only_last_n_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After MAX_RETENTION+3 snapshots, only MAX_RETENTION newest remain."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    backup_root = _isolate_backup_dir(monkeypatch, tmp_path)

    # Pre-populate stale snapshots with strictly older timestamps so
    # the new ones (created by snapshot_pre_bump below) sort newest.
    # Names follow ``<utc_ts>-<slug>-<label>.tar.gz`` for the sweep to
    # find them.
    slug = _project_slug(project)
    backup_root.mkdir(parents=True, exist_ok=True)
    for i in range(MAX_RETENTION + 3):
        stale = backup_root / f"20200101T0000{i:02d}Z-{slug}-bump.tar.gz"
        stale.write_bytes(b"old")

    # One real call — retention sweep happens at top of snapshot_pre_bump.
    snap = snapshot_pre_bump(project)
    assert snap is not None

    remaining = sorted(p.name for p in backup_root.iterdir() if f"-{slug}-" in p.name)
    assert len(remaining) == MAX_RETENTION


def test_retention_isolates_per_project_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bump in project A must NOT unlink project B's snapshots."""
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    _seed_project(project_a)
    _seed_project(project_b)
    backup_root = _isolate_backup_dir(monkeypatch, tmp_path)
    backup_root.mkdir(parents=True, exist_ok=True)

    slug_b = _project_slug(project_b)
    # Plant 15 stale B snapshots — sweep run for A must leave them alone.
    for i in range(MAX_RETENTION + 5):
        stale = backup_root / f"20200101T0000{i:02d}Z-{slug_b}-bump.tar.gz"
        stale.write_bytes(b"old-b")

    snap_a = snapshot_pre_bump(project_a)
    assert snap_a is not None

    b_remaining = [p for p in backup_root.iterdir() if f"-{slug_b}-" in p.name]
    assert len(b_remaining) == MAX_RETENTION + 5


def test_sweep_swallows_unlink_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flaky unlink (concurrent removal, permission flap) must NOT
    block the new snapshot from being written."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    backup_root = _isolate_backup_dir(monkeypatch, tmp_path)
    slug = _project_slug(project)
    backup_root.mkdir(parents=True, exist_ok=True)
    for i in range(MAX_RETENTION + 1):
        (backup_root / f"20200101T0000{i:02d}Z-{slug}-bump.tar.gz").write_bytes(b"x")

    original_unlink = Path.unlink

    def flaky_unlink(self: Path, missing_ok: bool = False) -> None:  # noqa: ARG001
        raise OSError("simulated flap")

    with patch.object(Path, "unlink", flaky_unlink):
        # Sweep raises internally on every candidate but is swallowed —
        # snapshot must still complete.
        snap = snapshot_pre_bump(project)

    # Restore real unlink for cleanup.
    Path.unlink = original_unlink  # type: ignore[method-assign]
    assert snap is not None
    assert snap.exists()


# ---------- Filename + stderr UX ----------


def test_filename_carries_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The label argument lands in the filename so users can grep by
    which entry-point fired the snapshot."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    _isolate_backup_dir(monkeypatch, tmp_path)

    snap = snapshot_pre_bump(project, label="init")
    assert snap is not None
    assert snap.name.endswith("-init.tar.gz")


def test_path_and_recovery_hint_printed_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC1: the path must be visible BEFORE any destructive work runs.
    Stderr is the surface; bump callers read it."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    _isolate_backup_dir(monkeypatch, tmp_path)

    snap = snapshot_pre_bump(project)
    captured = capsys.readouterr()
    assert snap is not None
    assert str(snap) in captured.err
    assert "tar -xzf" in captured.err


def test_project_slug_is_deterministic_and_resolved(
    tmp_path: Path,
) -> None:
    """The slug depends on the RESOLVED absolute path so relative-path
    callers get the same retention bucket as absolute-path callers."""
    project = tmp_path / "proj"
    project.mkdir()
    slug_abs = _project_slug(project)
    slug_rel_via_resolve = _project_slug(Path(str(project)))
    assert slug_abs == slug_rel_via_resolve
    assert len(slug_abs) == 8


def test_scope_paths_includes_critical_files() -> None:
    """Constants sanity: the load-bearing surface (settings.json,
    ai-hats.yaml, .agent/) MUST be in the declared scope. Regression
    guard against accidentally dropping an entry."""
    assert ".agent" in BACKUP_SCOPE_PATHS
    assert ".claude/settings.json" in BACKUP_SCOPE_PATHS
    assert "ai-hats.yaml" in BACKUP_SCOPE_PATHS


# ---------- Exclusions (.venv, __pycache__, bytecode) ----------


def test_venv_and_pycache_excluded_from_tarball(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The framework venv lives at <ai_hats_dir>/.venv and is regenerable
    via pip install — 85 MB of irrelevance if included. __pycache__ and
    .pyc are bytecode. .cache is per-session ephemera. None of these
    enter the tarball."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    venv = project / ".agent" / "ai-hats" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n")
    pycache = project / ".agent" / "ai-hats" / "__pycache__"
    pycache.mkdir()
    (pycache / "x.pyc").write_text("bytecode")
    cache = project / ".agent" / "ai-hats" / ".cache" / "session-123"
    cache.mkdir(parents=True)
    (cache / "scratch.log").write_text("ephemeral")
    # Loose .pyc file (not under __pycache__)
    (project / ".agent" / "stale.pyc").write_text("loose")
    _isolate_backup_dir(monkeypatch, tmp_path)

    snap = snapshot_pre_bump(project)
    assert snap is not None
    with tarfile.open(snap, "r:gz") as tar:
        names = set(tar.getnames())
    assert not any(".venv" in n.split("/") for n in names)
    assert not any("__pycache__" in n.split("/") for n in names)
    assert not any(".cache" in n.split("/") for n in names)
    assert not any(n.endswith(".pyc") for n in names)
    # Sanity: non-excluded content still present.
    assert "ai-hats.yaml" in names
    assert ".agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh" in names


def test_exclusion_does_not_drop_legitimate_dotfiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Don't over-prune: .manifest, .ai-hats-managed, .gitignore are
    framework bookkeeping that MUST survive."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_project(project)
    hooks = project / ".agent" / "ai-hats" / "library" / "hooks"
    (hooks / ".manifest").write_text("pre_bash_shared_state_guard.sh\n")
    (hooks / ".ai-hats-managed").write_text("legacy\n")
    _isolate_backup_dir(monkeypatch, tmp_path)

    snap = snapshot_pre_bump(project)
    assert snap is not None
    with tarfile.open(snap, "r:gz") as tar:
        names = set(tar.getnames())
    assert ".agent/ai-hats/library/hooks/.manifest" in names
    assert ".agent/ai-hats/library/hooks/.ai-hats-managed" in names
    assert ".gitignore" in names
