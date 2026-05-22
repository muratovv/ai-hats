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


def _entry(
    installed: str,
    latest: str,
    *,
    age_hours: int = 0,
    behind: int | None = None,
    ahead: int | None = None,
    installed_label: str | None = None,
    latest_label: str | None = None,
) -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        installed_sha=installed,
        latest_sha=latest,
        remote_url="https://github.com/muratovv/ai-hats.git",
        behind=behind,
        ahead=ahead,
        installed_label=installed_label,
        latest_label=latest_label,
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
    original = _entry(
        "a" * 40,
        "b" * 40,
        behind=19,
        ahead=0,
        installed_label="v0.6.0",
        latest_label="v0.6.0-19-gabcdef0",
    )
    write_cache(tmp_path, original)
    loaded = read_cache(tmp_path)
    assert loaded is not None
    assert loaded.installed_sha == original.installed_sha
    assert loaded.latest_sha == original.latest_sha
    assert loaded.remote_url == original.remote_url
    assert loaded.behind == 19
    assert loaded.ahead == 0
    assert loaded.installed_label == "v0.6.0"
    assert loaded.latest_label == "v0.6.0-19-gabcdef0"
    # Allow microsecond-level diff from serialization, but seconds must match.
    delta = abs((loaded.checked_at - original.checked_at).total_seconds())
    assert delta < 1.0


def test_is_fresh_true_within_24h(tmp_path):
    fresh = _entry("a", "b", age_hours=12)
    assert fresh.is_fresh is True


def test_is_fresh_false_past_24h(tmp_path):
    stale = _entry("a", "b", age_hours=25)
    assert stale.is_fresh is False


def test_has_update_true_when_strictly_behind():
    # Clean update available — latest has 19 commits installed lacks,
    # installed has none latest lacks.
    assert _entry("aaa", "bbb", behind=19, ahead=0).has_update is True


def test_has_update_false_when_installed_ahead():
    # Maintainer's HEAD past upstream master (HATS-432 reproducer).
    assert _entry("aaa", "bbb", behind=0, ahead=5).has_update is False


def test_has_update_false_when_diverged():
    # Both sides carry unique commits — no clean fast-forward target.
    assert _entry("aaa", "bbb", behind=3, ahead=2).has_update is False


def test_has_update_false_when_identical_counts():
    # behind=ahead=0 — equal tips, nothing to advertise.
    assert _entry("aaa", "aaa", behind=0, ahead=0).has_update is False


def test_has_update_false_when_counts_unknown():
    # Probe couldn't compute ahead/behind (non-git install, fetch failure):
    # default to "no update" rather than guessing from SHA inequality.
    assert _entry("aaa", "bbb").has_update is False
    assert _entry("aaa", "bbb", behind=5, ahead=None).has_update is False
    assert _entry("aaa", "bbb", behind=None, ahead=0).has_update is False


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


def test_legacy_cache_without_counts_parses_but_suppresses_banner(tmp_path, monkeypatch):
    """A pre-HATS-432 cache file (no behind/ahead/labels) must still load
    so the entry's ``checked_at`` keeps informing TTL — but ``has_update``
    stays False until the next probe rewrites with the new schema. This
    is the migration path; no explicit cache wipe needed.
    """
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    p = cache_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"checked_at": "2026-05-19T10:00:00Z", "installed_sha": "aaa", '
        '"latest_sha": "bbb", "remote_url": "https://example.git"}'
    )
    loaded = read_cache(tmp_path)
    assert loaded is not None
    assert loaded.installed_sha == "aaa"
    assert loaded.behind is None
    assert loaded.ahead is None
    assert loaded.installed_label is None
    assert loaded.has_update is False
