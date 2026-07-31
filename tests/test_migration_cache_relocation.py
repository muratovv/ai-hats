"""Migration step 9: carry the durable cache members out of the workspace (HATS-1398).

Only ``probe-mirror`` and ``update-check.json` move. The mirror is a bare git
clone — on this repo's own checkout, 202 MB — so leaving it behind would cost a
full refetch on the next probe. Per-session dirs are deliberately NOT moved: one
of them may belong to a session running right now, and the TTL sweep's legacy arm
drains them within the day.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.migrations import run_pending
from ai_hats.paths import PROJECT_CONFIG, ai_hats_dir, cache_root


def _project(tmp_path: Path) -> Path:
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\nmigration_step: 8\n"
    )
    return tmp_path


def _legacy_cache(project: Path) -> Path:
    legacy = ai_hats_dir(project) / ".cache"
    legacy.mkdir(parents=True, exist_ok=True)
    return legacy


def test_probe_mirror_and_update_check_leave_the_workspace(tmp_path):
    project = _project(tmp_path)
    legacy = _legacy_cache(project)
    (legacy / "probe-mirror").mkdir()
    (legacy / "probe-mirror" / "HEAD").write_text("ref: refs/heads/master\n")
    (legacy / "update-check.json").write_text('{"latest_sha": "abc"}')

    run_pending(Assembler(project))

    moved = cache_root(project)
    assert (moved / "probe-mirror" / "HEAD").read_text() == "ref: refs/heads/master\n"
    assert (moved / "update-check.json").read_text() == '{"latest_sha": "abc"}'
    assert not (legacy / "probe-mirror").exists()
    assert not (legacy / "update-check.json").exists()


def test_live_session_dirs_are_left_where_the_session_expects_them(tmp_path):
    """Yanking a running session's cache mid-flight is worse than a late sweep."""
    project = _project(tmp_path)
    legacy = _legacy_cache(project)
    live = legacy / "sessions" / "live-sid"
    live.mkdir(parents=True)
    (live / "prompt.md").write_text("body")

    run_pending(Assembler(project))

    assert (live / "prompt.md").read_text() == "body"


def test_an_already_relocated_cache_is_not_overwritten(tmp_path):
    """Re-running must not clobber the live root with a stale in-tree leftover."""
    project = _project(tmp_path)
    legacy = _legacy_cache(project)
    (legacy / "update-check.json").write_text('{"latest_sha": "stale"}')
    moved = cache_root(project)
    moved.mkdir(parents=True)
    (moved / "update-check.json").write_text('{"latest_sha": "fresh"}')

    run_pending(Assembler(project))

    assert (moved / "update-check.json").read_text() == '{"latest_sha": "fresh"}'


def test_a_project_without_a_legacy_cache_is_a_no_op(tmp_path):
    project = _project(tmp_path)

    run_pending(Assembler(project))  # must not raise

    assert not (ai_hats_dir(project) / ".cache").exists()
