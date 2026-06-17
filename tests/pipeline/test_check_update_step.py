"""Unit tests for the ``check_update_async`` pipeline step."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


from ai_hats.pipeline.steps.check_update import CheckUpdateAsync
from ai_hats.update_check.cache import CacheEntry, write_cache


def _fresh_entry() -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        installed_sha="a" * 40,
        latest_sha="b" * 40,
        remote_url="https://example.git",
    )


def _stale_entry() -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc) - timedelta(days=2),
        installed_sha="a" * 40,
        latest_sha="b" * 40,
        remote_url="https://example.git",
    )


def test_step_io_requires_project_dir():
    step = CheckUpdateAsync()
    assert "project_dir" in step.io.requires
    assert step.io.name == "check_update_async"


def test_step_is_continue_on_failure():
    assert CheckUpdateAsync.failure_policy == "continue"


def test_skips_spawn_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    step = CheckUpdateAsync()
    with patch("ai_hats.pipeline.steps.check_update.subprocess.Popen") as popen:
        result = step.run(project_dir=tmp_path)
    assert result == {}
    popen.assert_not_called()


def test_skips_spawn_when_cache_fresh_and_sha_matches(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _fresh_entry())  # installed_sha = "a" * 40
    step = CheckUpdateAsync()
    with patch(
        "ai_hats.pipeline.steps.check_update.detect_installed_sha",
        return_value="a" * 40,
    ), patch("ai_hats.pipeline.steps.check_update.subprocess.Popen") as popen:
        result = step.run(project_dir=tmp_path)
    assert result == {}
    popen.assert_not_called()


def test_skips_spawn_when_fresh_and_sha_unknown(tmp_path, monkeypatch):
    """Cannot detect the running SHA → do NOT churn a probe every session."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _fresh_entry())
    step = CheckUpdateAsync()
    with patch(
        "ai_hats.pipeline.steps.check_update.detect_installed_sha",
        return_value=None,
    ), patch("ai_hats.pipeline.steps.check_update.subprocess.Popen") as popen:
        step.run(project_dir=tmp_path)
    popen.assert_not_called()


def test_spawns_when_fresh_but_sha_changed(tmp_path, monkeypatch):
    """HATS-781: a reinstall within the 24h TTL changes the installed SHA — the
    fresh cache no longer describes the running build, so re-probe."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _fresh_entry())  # installed_sha = "a" * 40
    step = CheckUpdateAsync()
    with patch(
        "ai_hats.pipeline.steps.check_update.detect_installed_sha",
        return_value="b" * 40,
    ), patch("ai_hats.pipeline.steps.check_update.subprocess.Popen") as popen:
        step.run(project_dir=tmp_path)
    popen.assert_called_once()


def test_skips_spawn_when_local_channel(tmp_path, monkeypatch):
    """LOCAL editable harness → no background probe at all."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    (tmp_path / "ai-hats.yaml").write_text("harness:\n  channel: local\n  path: .\n")
    step = CheckUpdateAsync()
    with patch("ai_hats.pipeline.steps.check_update.subprocess.Popen") as popen:
        step.run(project_dir=tmp_path)
    popen.assert_not_called()


def test_spawns_when_cache_stale(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _stale_entry())
    step = CheckUpdateAsync()
    with patch("ai_hats.pipeline.steps.check_update.subprocess.Popen") as popen:
        step.run(project_dir=tmp_path)
    popen.assert_called_once()
    args, kwargs = popen.call_args
    cmd = args[0]
    assert cmd[1:] == ["-m", "ai_hats.update_check", str(tmp_path)]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] is __import__("subprocess").DEVNULL


def test_spawns_when_cache_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    step = CheckUpdateAsync()
    with patch("ai_hats.pipeline.steps.check_update.subprocess.Popen") as popen:
        step.run(project_dir=tmp_path)
    popen.assert_called_once()


def test_swallows_oserror_from_popen(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    step = CheckUpdateAsync()
    with patch(
        "ai_hats.pipeline.steps.check_update.subprocess.Popen",
        side_effect=OSError("python missing"),
    ):
        # Must not raise.
        result = step.run(project_dir=tmp_path)
    assert result == {}
