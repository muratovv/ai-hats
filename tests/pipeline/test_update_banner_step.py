"""Unit tests for the ``render_update_banner`` pipeline step."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_hats.pipeline.steps.update_banner import RenderUpdateBanner
from ai_hats.update_check.cache import CacheEntry, write_cache


def _entry_with_update() -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        latest_sha="9876543210fedcba9876543210fedcba98765432",
        remote_url="https://example.git",
    )


def _entry_no_update() -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha="a" * 40,
        latest_sha="a" * 40,
        remote_url="https://example.git",
    )


def test_step_io():
    step = RenderUpdateBanner()
    assert step.io.name == "render_update_banner"
    assert "project_dir" in step.io.requires


def test_step_is_continue_on_failure():
    assert RenderUpdateBanner.failure_policy == "continue"


def test_renders_banner_when_update_available(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_with_update())
    step = RenderUpdateBanner()
    step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ai-hats update available" in captured.err
    assert "a1b2c3d" in captured.err
    assert "9876543" in captured.err
    assert "ai-hats update" in captured.err
    # Discoverability: env var name must appear in the banner itself.
    assert "AI_HATS_NO_UPDATE_CHECK" in captured.err


def test_silent_when_no_update(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_no_update())
    step = RenderUpdateBanner()
    step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_silent_when_no_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    step = RenderUpdateBanner()
    step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_silent_when_disabled(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_with_update())
    step = RenderUpdateBanner()
    step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.err == ""
