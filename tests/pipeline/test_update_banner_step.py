"""Unit tests for the ``render_update_banner`` pipeline step."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch


from ai_hats.pipeline.steps.update_banner import RenderUpdateBanner
from ai_hats.update_check.cache import CacheEntry, write_cache

# Cached installed SHA used by the fixtures below; tests that want the banner to
# render patch ``detect_installed_sha`` to return this so the HATS-781 SHA-match
# gate sees the running build as the one the cache describes.
_ENTRY_INSTALLED_SHA = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


def _entry_with_update(
    *,
    installed_label: str | None = None,
    latest_label: str | None = None,
    behind: int = 19,
    ahead: int = 0,
) -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        latest_sha="9876543210fedcba9876543210fedcba98765432",
        remote_url="https://example.git",
        behind=behind,
        ahead=ahead,
        installed_label=installed_label,
        latest_label=latest_label,
    )


def _entry_no_update() -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha="a" * 40,
        latest_sha="a" * 40,
        remote_url="https://example.git",
        behind=0,
        ahead=0,
    )


def _entry_installed_ahead() -> CacheEntry:
    """HATS-432 reproducer: maintainer HEAD past cached upstream master."""
    return CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha="a" * 40,
        latest_sha="b" * 40,
        remote_url="https://example.git",
        behind=0,
        ahead=5,
    )


def _entry_diverged() -> CacheEntry:
    return CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha="a" * 40,
        latest_sha="b" * 40,
        remote_url="https://example.git",
        behind=3,
        ahead=2,
    )


def test_step_io():
    step = RenderUpdateBanner()
    assert step.io.name == "render_update_banner"
    assert "project_dir" in step.io.requires


def test_step_is_continue_on_failure():
    assert RenderUpdateBanner.failure_policy == "continue"


def test_renders_banner_when_update_available_fallback_shas(tmp_path, monkeypatch, capsys):
    """No describe labels — banner uses short SHAs + ``+<behind> commits`` suffix."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_with_update())
    step = RenderUpdateBanner()
    with patch(
        "ai_hats.pipeline.steps.update_banner.detect_installed_sha",
        return_value=_ENTRY_INSTALLED_SHA,
    ):
        step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ai-hats update available" in captured.err
    assert "a1b2c3d" in captured.err
    assert "9876543" in captured.err
    assert "+19 commits" in captured.err
    assert "ai-hats self update" in captured.err
    # Discoverability: env var name must appear in the banner itself.
    assert "AI_HATS_NO_UPDATE_CHECK" in captured.err


def test_renders_banner_with_describe_labels(tmp_path, monkeypatch, capsys):
    """When labels are present, banner shows them — and OMITS the ``+N commits``
    suffix because the label already conveys the delta (e.g. ``v0.6.0-19-g…``).
    """
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(
        tmp_path,
        _entry_with_update(
            installed_label="v0.6.0",
            latest_label="v0.6.0-19-gabcdef0",
            behind=19,
        ),
    )
    step = RenderUpdateBanner()
    with patch(
        "ai_hats.pipeline.steps.update_banner.detect_installed_sha",
        return_value=_ENTRY_INSTALLED_SHA,
    ):
        step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert "v0.6.0" in captured.err
    assert "v0.6.0-19-gabcdef0" in captured.err
    assert "+19 commits" not in captured.err
    # Short SHAs must NOT appear when labels are used.
    assert "a1b2c3d" not in captured.err
    assert "9876543" not in captured.err
    assert "ai-hats self update" in captured.err


def test_silent_when_no_update(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_no_update())
    step = RenderUpdateBanner()
    step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_silent_when_installed_ahead(tmp_path, monkeypatch, capsys):
    """HATS-432 regression: HEAD ahead of cached upstream must NOT fire banner."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_installed_ahead())
    step = RenderUpdateBanner()
    step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_silent_when_diverged(tmp_path, monkeypatch, capsys):
    """Both sides carry unique commits → no clean fast-forward → no banner."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_diverged())
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


# ---------- HATS-781: SHA-match + local-channel gates ----------


def test_silent_when_installed_sha_differs(tmp_path, monkeypatch, capsys):
    """Stale cache: the running build's SHA differs from the cached
    ``installed_sha`` — never render a banner about a build you are not
    running (the reported delta would be for the wrong commit)."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_with_update())
    step = RenderUpdateBanner()
    with patch(
        "ai_hats.pipeline.steps.update_banner.detect_installed_sha",
        return_value="f" * 40,
    ):
        step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_renders_when_installed_sha_matches_short(tmp_path, monkeypatch, capsys):
    """Prefix-tolerant: a 9-char baked SHA matches its full rev-parse form."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_with_update())
    step = RenderUpdateBanner()
    with patch(
        "ai_hats.pipeline.steps.update_banner.detect_installed_sha",
        return_value=_ENTRY_INSTALLED_SHA[:9],
    ):
        step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert "ai-hats update available" in captured.err


def test_renders_when_installed_sha_unknown(tmp_path, monkeypatch, capsys):
    """Cannot detect the running SHA → preserve prior behaviour (render)."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    write_cache(tmp_path, _entry_with_update())
    step = RenderUpdateBanner()
    with patch(
        "ai_hats.pipeline.steps.update_banner.detect_installed_sha",
        return_value=None,
    ):
        step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert "ai-hats update available" in captured.err


def test_silent_when_local_channel(tmp_path, monkeypatch, capsys):
    """LOCAL editable harness → banner hidden even with a matching update."""
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    (tmp_path / "ai-hats.yaml").write_text("harness:\n  channel: local\n  path: .\n")
    write_cache(tmp_path, _entry_with_update())
    step = RenderUpdateBanner()
    with patch(
        "ai_hats.pipeline.steps.update_banner.detect_installed_sha",
        return_value=_ENTRY_INSTALLED_SHA,
    ):
        step.run(project_dir=tmp_path)
    captured = capsys.readouterr()
    assert captured.err == ""
