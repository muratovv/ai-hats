"""Unit tests for the update-check cache layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_hats.update_check.cache import (
    CacheEntry,
    cache_path,
    read_cache,
    write_cache,
)


def _entry(installed: str, latest: str, *, age_hours: int = 0) -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        installed_sha=installed,
        latest_sha=latest,
        remote_url="https://github.com/muratovv/ai-hats.git",
    )


def test_cache_path_is_under_ai_hats_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    p = cache_path(tmp_path)
    assert p == tmp_path / "ai-hats-data" / ".cache" / "update-check.json"


def test_read_cache_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    assert read_cache(tmp_path) is None


def test_write_then_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    original = _entry("a" * 40, "b" * 40)
    write_cache(tmp_path, original)
    loaded = read_cache(tmp_path)
    assert loaded is not None
    assert loaded.installed_sha == original.installed_sha
    assert loaded.latest_sha == original.latest_sha
    assert loaded.remote_url == original.remote_url
    # Allow microsecond-level diff from serialization, but seconds must match.
    delta = abs((loaded.checked_at - original.checked_at).total_seconds())
    assert delta < 1.0


def test_is_fresh_true_within_24h(tmp_path):
    fresh = _entry("a", "b", age_hours=12)
    assert fresh.is_fresh is True


def test_is_fresh_false_past_24h(tmp_path):
    stale = _entry("a", "b", age_hours=25)
    assert stale.is_fresh is False


def test_has_update_true_on_mismatch():
    assert _entry("aaa", "bbb").has_update is True


def test_has_update_false_on_match():
    assert _entry("aaa", "aaa").has_update is False


def test_has_update_false_when_empty():
    assert _entry("", "bbb").has_update is False
    assert _entry("aaa", "").has_update is False


def test_corrupt_cache_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    p = cache_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json")
    assert read_cache(tmp_path) is None


def test_missing_key_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    p = cache_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Missing remote_url — should fail the schema check gracefully.
    p.write_text('{"checked_at": "2026-05-19T10:00:00Z", "installed_sha": "x", "latest_sha": "y"}')
    assert read_cache(tmp_path) is None
