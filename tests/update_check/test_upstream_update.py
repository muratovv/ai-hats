"""Unit tests for the canonical ``upstream_update`` predicate (HATS-846).

``upstream_update`` is the single home for the "is the running build behind
upstream?" question — it bundles ``is_local_channel`` + ``read_cache`` +
``has_update`` + running-SHA ``sha_matches`` so the banner and hook self-heal
cannot diverge on the guard set again.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ai_hats.update_check import upstream_update
from ai_hats.update_check.cache import CacheEntry, write_cache

_SHA = "a" * 40


def _seed_cache(project_dir, *, installed_sha=_SHA, behind=7, ahead=0) -> None:
    write_cache(
        project_dir,
        CacheEntry(
            checked_at=datetime.now(timezone.utc),
            installed_sha=installed_sha,
            latest_sha="b" * 40,
            remote_url="https://example.git",
            behind=behind,
            ahead=ahead,
        ),
    )


def _patch_detect(monkeypatch, value) -> None:
    # `upstream_update` resolves `detect_installed_sha` from the update_check
    # package namespace — patch it there (not in checker).
    monkeypatch.setattr("ai_hats.update_check.detect_installed_sha", lambda: value)


def test_local_channel_returns_none(tmp_path, monkeypatch):
    """LOCAL is git-driven — 'behind upstream' is meaningless, never suppress heal."""
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "data"))
    (tmp_path / "ai-hats.yaml").write_text("harness:\n  channel: local\n  path: .\n")
    _seed_cache(tmp_path)
    _patch_detect(monkeypatch, _SHA)  # even matching SHA must not matter
    assert upstream_update(tmp_path) is None


def test_no_cache_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "data"))
    _patch_detect(monkeypatch, _SHA)
    assert upstream_update(tmp_path) is None


def test_behind_with_matching_sha_returns_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "data"))
    _seed_cache(tmp_path, installed_sha=_SHA, behind=7, ahead=0)
    _patch_detect(monkeypatch, _SHA)
    entry = upstream_update(tmp_path)
    assert entry is not None
    assert entry.behind == 7


def test_behind_with_foreign_sha_returns_none(tmp_path, monkeypatch):
    """Cache describes a build we are not running → not our 'behind'."""
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "data"))
    _seed_cache(tmp_path, installed_sha=_SHA, behind=7, ahead=0)
    _patch_detect(monkeypatch, "f" * 40)
    assert upstream_update(tmp_path) is None


def test_not_behind_returns_none(tmp_path, monkeypatch):
    """installed ahead of cached upstream → has_update False → None."""
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "data"))
    _seed_cache(tmp_path, installed_sha=_SHA, behind=0, ahead=5)
    _patch_detect(monkeypatch, _SHA)
    assert upstream_update(tmp_path) is None


def test_unknown_current_sha_returns_entry(tmp_path, monkeypatch):
    """Running SHA unknown (detect → None) → treat the entry as about-us,
    NOT suppressed — mirrors the banner's long-standing behaviour."""
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "data"))
    _seed_cache(tmp_path, installed_sha=_SHA, behind=7, ahead=0)
    _patch_detect(monkeypatch, None)
    assert upstream_update(tmp_path) is not None
