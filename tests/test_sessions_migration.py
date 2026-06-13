"""Session-class layout migration: `.gitlog/` + `.agent/{retros,...}/` → `<dir>/sessions/` (HATS-312)."""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.paths import (
    audits_dir,
    handoffs_dir,
    retros_dir,
    runs_dir,
    sessions_dir,
    worktrees_dir,
)


def _seed_session_legacy(project_dir: Path) -> dict[str, Path]:
    """Populate every legacy session-class path with a marker file."""
    (project_dir / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    seeds: dict[str, Path] = {}
    # .gitlog/ with pipeline_runs + a session_ subdir
    pl = project_dir / ".gitlog" / "pipeline_runs" / "execute" / "run-1"
    pl.mkdir(parents=True)
    (pl / "manifest.yaml").write_text("name: run-1")
    seeds["pipeline"] = pl / "manifest.yaml"
    sess = project_dir / ".gitlog" / "session_20260101-000000-1"
    sess.mkdir(parents=True)
    (sess / "metrics.json").write_text("{}")
    seeds["session"] = sess / "metrics.json"
    # retros / audits / handoffs / experiments
    for name, sub in [
        ("retro", "retrospectives"),
        ("audit", "audits"),
        ("handoff", "handoffs"),
    ]:
        d = project_dir / ".agent" / sub
        d.mkdir(parents=True)
        f = d / "2026-01-01-marker.md"
        f.write_text("legacy")
        seeds[name] = f
    exp = project_dir / ".agent" / "experiments" / "lab-a"
    exp.mkdir(parents=True)
    (exp / "README.md").write_text("exp")
    seeds["experiment"] = exp / "README.md"
    # worktrees + singleton
    wts = project_dir / ".agent" / "worktrees"
    wts.mkdir(parents=True)
    (wts / "task-hats-001.json").write_text('{"branch":"task/hats-001"}')
    seeds["worktree_state"] = wts / "task-hats-001.json"
    singleton = project_dir / ".agent" / "worktree.json"
    singleton.write_text('{"branch":"task/old"}')
    seeds["worktree_singleton"] = singleton
    # Orphan handoff at .agent/ root
    orphan = project_dir / ".agent" / "handoff-2026-04-09-hats-061.md"
    orphan.write_text("orphan")
    seeds["orphan"] = orphan
    return seeds


def test_sessions_migration_moves_all_paths(tmp_path: Path) -> None:
    seeds = _seed_session_legacy(tmp_path)
    asm = Assembler(tmp_path)

    asm._migrate_layout_v4_sessions()

    # Pipeline run moved into sessions/runs/pipeline_runs/...
    assert (runs_dir(tmp_path) / "pipeline_runs" / "execute" / "run-1" / "manifest.yaml").exists()
    # session_<id>/ trace dir moved into sessions/runs/session_.../
    assert (runs_dir(tmp_path) / "session_20260101-000000-1" / "metrics.json").exists()
    # Per-class .agent/ subdirs landed under sessions/{retros,audits,handoffs,experiments}/
    assert (retros_dir(tmp_path) / "2026-01-01-marker.md").exists()
    assert (audits_dir(tmp_path) / "2026-01-01-marker.md").exists()
    assert (handoffs_dir(tmp_path) / "2026-01-01-marker.md").exists()
    assert (sessions_dir(tmp_path) / "experiments" / "lab-a" / "README.md").exists()
    # Worktrees + singleton
    assert (worktrees_dir(tmp_path) / "task-hats-001.json").exists()
    assert (sessions_dir(tmp_path) / "worktree.json").exists()
    # Orphan handoff picked up
    assert (handoffs_dir(tmp_path) / "handoff-2026-04-09-hats-061.md").exists()
    # All legacy roots gone (or empty)
    assert not (tmp_path / ".gitlog").exists()
    for sub in ("retrospectives", "audits", "handoffs", "experiments", "worktrees", "worktree.json"):
        assert not (tmp_path / ".agent" / sub).exists(), f".agent/{sub} still present"
    assert not seeds["orphan"].exists()


def test_sessions_migration_idempotent(tmp_path: Path) -> None:
    """Running migration twice is a no-op the second time."""
    _seed_session_legacy(tmp_path)
    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_sessions()
    # Snapshot the new layout, run again, expect unchanged content.
    before = {
        p.relative_to(tmp_path): p.read_bytes() if p.is_file() else None
        for p in (tmp_path / ".agent" / "ai-hats" / "sessions").rglob("*")
    }
    asm._migrate_layout_v4_sessions()
    after = {
        p.relative_to(tmp_path): p.read_bytes() if p.is_file() else None
        for p in (tmp_path / ".agent" / "ai-hats" / "sessions").rglob("*")
    }
    assert before == after


def test_sessions_migration_merge_when_target_exists(tmp_path: Path) -> None:
    """Pre-existing files at the new location are preserved; partial old content merges in."""
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    # Legacy: two files
    old = tmp_path / ".agent" / "retrospectives"
    old.mkdir(parents=True)
    (old / "old-a.md").write_text("from old")
    (old / "old-b.md").write_text("from old")
    # New side already has a same-name file with different content
    new = retros_dir(tmp_path)
    new.mkdir(parents=True)
    (new / "old-a.md").write_text("from new (winner)")

    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_sessions()

    assert (new / "old-a.md").read_text() == "from new (winner)"
    assert (new / "old-b.md").read_text() == "from old"
    assert not old.exists()


def test_sessions_migration_noop_on_clean_project(tmp_path: Path) -> None:
    """No legacy paths → nothing created or raised."""
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_sessions()  # must not raise
    # No sessions/ subtree spawned by migration alone.
    assert not (tmp_path / ".agent" / "ai-hats" / "sessions").exists() or \
        not any((tmp_path / ".agent" / "ai-hats" / "sessions").iterdir())
