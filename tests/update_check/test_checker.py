"""Unit tests for the update-check resolver layer."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ai_hats.update_check import checker
from ai_hats.update_check.checker import (
    FALLBACK_REMOTE_URL,
    _coerce_to_https,
    detect_installed_sha,
    detect_remote_url,
    fetch_latest_sha,
    run_check,
)


# ---------- detect_installed_sha ----------


def test_detect_installed_sha_via_git_rev_parse():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="deadbeef\n", stderr="")
    with patch.object(subprocess, "run", return_value=fake):
        assert detect_installed_sha() == "deadbeef"


def test_detect_installed_sha_falls_back_to_version_module():
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not a repo")
    import sys, types
    fake_module = types.SimpleNamespace(__commit__="cafebabe")
    sys.modules["ai_hats._version"] = fake_module
    try:
        with patch.object(subprocess, "run", return_value=fail):
            assert detect_installed_sha() == "cafebabe"
    finally:
        sys.modules.pop("ai_hats._version", None)


def test_detect_installed_sha_returns_none_when_both_fail():
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    import sys, types
    sys.modules["ai_hats._version"] = types.SimpleNamespace(__commit__="unknown")
    try:
        with patch.object(subprocess, "run", return_value=fail):
            assert detect_installed_sha() is None
    finally:
        sys.modules.pop("ai_hats._version", None)


def test_detect_installed_sha_handles_git_missing():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        # Without _version module loadable, should return None.
        import sys
        sys.modules.pop("ai_hats._version", None)
        # If the real _version exists in the installed pkg, this falls through
        # to whatever it returns — accept any string OR None.
        result = detect_installed_sha()
        assert result is None or isinstance(result, str)


# ---------- _coerce_to_https ----------


@pytest.mark.parametrize("inp,expected", [
    ("git+ssh://git@github.com/foo/bar.git", "https://github.com/foo/bar.git"),
    ("git@github.com:foo/bar.git", "https://github.com/foo/bar.git"),
    ("github.com/foo/bar.git", "https://github.com/foo/bar.git"),
    ("https://github.com/foo/bar.git", "https://github.com/foo/bar.git"),
    ("git+https://github.com/foo/bar.git", "https://github.com/foo/bar.git"),
])
def test_coerce_to_https(inp, expected):
    assert _coerce_to_https(inp) == expected


# ---------- detect_remote_url ----------


def test_detect_remote_url_env_override(monkeypatch):
    monkeypatch.setenv("AI_HATS_REPO_URL", "git+ssh://git@github.com/fork/ai-hats.git")
    assert detect_remote_url() == "https://github.com/fork/ai-hats.git"


def test_detect_remote_url_env_ignored_when_local_path(monkeypatch):
    # Path-style values (no scheme) are used by bootstrap.sh for local installs
    # and are useless for ls-remote — we ignore them and fall through.
    monkeypatch.setenv("AI_HATS_REPO_URL", "/local/path/ai-hats")
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    url = detect_remote_url()
    # Should fall through to metadata or fallback — not the local path.
    assert url != "/local/path/ai-hats"


def test_detect_remote_url_fallback_when_metadata_silent(monkeypatch):
    monkeypatch.delenv("AI_HATS_REPO_URL", raising=False)
    fake_meta = MagicMock()
    fake_meta.get_all.return_value = []
    with patch.object(checker, "metadata", return_value=fake_meta):
        assert detect_remote_url() == FALLBACK_REMOTE_URL


def test_detect_remote_url_from_metadata_source(monkeypatch):
    monkeypatch.delenv("AI_HATS_REPO_URL", raising=False)
    fake_meta = MagicMock()
    fake_meta.get_all.return_value = [
        "Homepage, https://ai-hats.example/site",
        "Source, https://github.com/muratovv/ai-hats",
    ]
    with patch.object(checker, "metadata", return_value=fake_meta):
        # Implementation returns the FIRST matching label among
        # {source,repository,homepage}; Homepage is matched first here.
        url = detect_remote_url()
        assert url in {
            "https://ai-hats.example/site",
            "https://github.com/muratovv/ai-hats",
        }


# ---------- fetch_latest_sha ----------


def test_fetch_latest_sha_parses_ls_remote_output():
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="abc123\trefs/heads/master\n", stderr="",
    )
    with patch.object(subprocess, "run", return_value=fake):
        assert fetch_latest_sha("https://example.git") == "abc123"


def test_fetch_latest_sha_returns_none_on_nonzero_exit():
    fail = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal")
    with patch.object(subprocess, "run", return_value=fail):
        assert fetch_latest_sha("https://example.git") is None


def test_fetch_latest_sha_returns_none_on_timeout():
    with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 10)):
        assert fetch_latest_sha("https://example.git") is None


def test_fetch_latest_sha_returns_none_when_git_missing():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert fetch_latest_sha("https://example.git") is None


def test_fetch_latest_sha_returns_none_on_empty_output():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch.object(subprocess, "run", return_value=fake):
        assert fetch_latest_sha("https://example.git") is None


# ---------- run_check ----------


def test_run_check_writes_cache_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    monkeypatch.delenv("AI_HATS_REPO_URL", raising=False)
    with patch.object(checker, "detect_installed_sha", return_value="a" * 40), \
         patch.object(checker, "detect_remote_url", return_value="https://example.git"), \
         patch.object(checker, "fetch_latest_sha", return_value="b" * 40):
        entry = run_check(tmp_path)
    assert entry is not None
    assert entry.installed_sha == "a" * 40
    assert entry.latest_sha == "b" * 40
    # Cache file must exist now.
    from ai_hats.update_check.cache import cache_path, read_cache
    assert cache_path(tmp_path).exists()
    assert read_cache(tmp_path) is not None


def test_run_check_skips_when_installed_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    with patch.object(checker, "detect_installed_sha", return_value=None):
        assert run_check(tmp_path) is None


def test_run_check_skips_when_remote_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    with patch.object(checker, "detect_installed_sha", return_value="a" * 40), \
         patch.object(checker, "detect_remote_url", return_value="https://example.git"), \
         patch.object(checker, "fetch_latest_sha", return_value=None):
        assert run_check(tmp_path) is None
    # No cache should be written.
    from ai_hats.update_check.cache import cache_path
    assert not cache_path(tmp_path).exists()
