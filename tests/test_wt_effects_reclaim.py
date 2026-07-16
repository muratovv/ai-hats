"""Pending-hunk-review guard for the epicification worktree reclaim (HATS-979/818).

``git status`` ignores the gitignored ``.hunk/notes.json``, so the reclaim's
git-only cleanliness check would tear down a worktree whose human review is not
yet addressed. ``_has_pending_hunk_review`` is the guard that keeps it.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.wt_effects import _has_pending_hunk_review


def _write_notes(worktree: Path, content: str) -> Path:
    (worktree / ".hunk").mkdir(parents=True, exist_ok=True)
    (worktree / ".hunk" / "notes.json").write_text(content)
    return worktree


def test_pending_review_is_detected(tmp_path: Path) -> None:
    wt = _write_notes(tmp_path / "wt", '[{"id": "u:1", "text": "fix this"}]')
    assert _has_pending_hunk_review(wt) is True


def test_drained_or_absent_notes_are_not_pending(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _has_pending_hunk_review(empty) is False  # no .hunk dir at all

    drained = _write_notes(tmp_path / "drained", "[]")  # consumed → empty array
    assert _has_pending_hunk_review(drained) is False


def test_none_worktree_is_not_pending() -> None:
    assert _has_pending_hunk_review(None) is False
