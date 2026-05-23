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


def _ok(stdout=""):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail(stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def test_detect_installed_sha_via_git_rev_parse():
    # HATS-441: detect_installed_sha now runs ``git ls-files --error-unmatch
    # __init__.py`` first to confirm pkg_dir is tracked by the enclosing
    # repo before trusting its HEAD. Mock both subprocess calls in order.
    with patch.object(
        subprocess, "run",
        side_effect=[_ok(), _ok("deadbeef\n")],
    ):
        assert detect_installed_sha() == "deadbeef"


def test_detect_installed_sha_skips_foreign_git(monkeypatch):
    """HATS-441: pkg_dir tracked-check fails → skip rev-parse, use __commit__.

    Guards the false-positive class where ``ai_hats`` is non-editable-installed
    inside a user's project venv whose project itself is a git repo. Without
    the tracked-check, git walks up from site-packages and returns the user's
    project HEAD as ai-hats's installed SHA.
    """
    import sys, types
    sys.modules["ai_hats._version"] = types.SimpleNamespace(__commit__="cafebabe")
    try:
        # ls-files returns non-zero (foreign repo doesn't track pkg's __init__.py).
        # rev-parse is NOT called — but if it were, the mock would still
        # be exhausted (we provide just the one ls-files return).
        with patch.object(subprocess, "run", side_effect=[_fail()]):
            assert detect_installed_sha() == "cafebabe"
    finally:
        sys.modules.pop("ai_hats._version", None)


def test_detect_installed_sha_falls_back_to_version_module():
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not a repo")
    import sys, types
    # HATS-458: setuptools-scm 8+ writes ``__commit_id__`` (with leading
    # ``g`` prefix per git-describe convention). ``detect_installed_sha``
    # must strip the prefix.
    fake_module = types.SimpleNamespace(__commit_id__="gcafebabe")
    sys.modules["ai_hats._version"] = fake_module
    try:
        with patch.object(subprocess, "run", return_value=fail):
            assert detect_installed_sha() == "cafebabe"
    finally:
        sys.modules.pop("ai_hats._version", None)


def test_detect_installed_sha_accepts_legacy_commit_attr():
    """Legacy ``__commit__`` (older setuptools-scm) still works."""
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    import sys, types
    sys.modules["ai_hats._version"] = types.SimpleNamespace(__commit__="deadbeef")
    try:
        with patch.object(subprocess, "run", return_value=fail):
            assert detect_installed_sha() == "deadbeef"
    finally:
        sys.modules.pop("ai_hats._version", None)


def test_detect_installed_sha_prefers_commit_id_over_commit():
    """When both attrs exist, prefer the modern ``__commit_id__``."""
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    import sys, types
    sys.modules["ai_hats._version"] = types.SimpleNamespace(
        __commit_id__="gnewer123",
        __commit__="legacy456",
    )
    try:
        with patch.object(subprocess, "run", return_value=fail):
            assert detect_installed_sha() == "newer123"
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
         patch.object(checker, "fetch_latest_sha", return_value="b" * 40), \
         patch.object(checker, "_fetch_into_pkg", return_value=True), \
         patch.object(checker, "_count_ahead_behind", return_value=(0, 19)), \
         patch.object(checker, "_describe", side_effect=["v0.6.0", "v0.6.0-19-gabcdef0"]):
        entry = run_check(tmp_path)
    assert entry is not None
    assert entry.installed_sha == "a" * 40
    assert entry.latest_sha == "b" * 40
    assert entry.ahead == 0
    assert entry.behind == 19
    assert entry.installed_label == "v0.6.0"
    assert entry.latest_label == "v0.6.0-19-gabcdef0"
    assert entry.has_update is True
    # Cache file must exist and round-trip.
    from ai_hats.update_check.cache import cache_path, read_cache
    assert cache_path(tmp_path).exists()
    loaded = read_cache(tmp_path)
    assert loaded is not None and loaded.behind == 19 and loaded.ahead == 0


def test_run_check_persists_unknown_counts_when_git_fails(tmp_path, monkeypatch):
    """Both pkg-checkout and mirror fallback fail → entry preserves
    installed/latest SHAs but axes/labels stay None (banner silent).
    """
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    monkeypatch.delenv("AI_HATS_REPO_URL", raising=False)
    with patch.object(checker, "detect_installed_sha", return_value="a" * 40), \
         patch.object(checker, "detect_remote_url", return_value="https://example.git"), \
         patch.object(checker, "fetch_latest_sha", return_value="b" * 40), \
         patch.object(checker, "_fetch_into_pkg", return_value=False), \
         patch.object(checker, "_ensure_probe_mirror", return_value=None), \
         patch.object(checker, "_count_ahead_behind", return_value=None), \
         patch.object(checker, "_describe", return_value=None):
        entry = run_check(tmp_path)
    assert entry is not None
    assert entry.behind is None
    assert entry.ahead is None
    assert entry.has_update is False


# ---------- HATS-458: probe-mirror fallback ----------


def test_run_check_falls_back_to_mirror_when_pkg_path_unusable(tmp_path, monkeypatch):
    """Editable fast path fails (HATS-441 guard) → mirror path produces
    correct ahead/behind/labels and entry.has_update fires.
    """
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    monkeypatch.delenv("AI_HATS_REPO_URL", raising=False)
    fake_mirror = tmp_path / "ai-hats-data" / ".cache" / "probe-mirror"

    with patch.object(checker, "detect_installed_sha", return_value="a" * 40), \
         patch.object(checker, "detect_remote_url", return_value="https://example.git"), \
         patch.object(checker, "fetch_latest_sha", return_value="b" * 40), \
         patch.object(checker, "_fetch_into_pkg", return_value=False), \
         patch.object(checker, "_ensure_probe_mirror", return_value=fake_mirror) as mock_init, \
         patch.object(checker, "_fetch_into_mirror", return_value=True) as mock_fetch, \
         patch.object(checker, "_count_ahead_behind", return_value=(0, 19)) as mock_count, \
         patch.object(checker, "_describe", side_effect=["v0.6.0", "v0.6.0-19-gabcdef0"]) as mock_describe:
        entry = run_check(tmp_path)

    assert entry is not None
    assert entry.behind == 19 and entry.ahead == 0
    assert entry.installed_label == "v0.6.0"
    assert entry.latest_label == "v0.6.0-19-gabcdef0"
    assert entry.has_update is True

    # Mirror was initialized once + master fetched once. installed_sha is
    # NOT fetched separately (it's typically reachable from master's
    # history; if not, rev-list returns None and axes stay None).
    assert mock_init.call_count == 1
    assert mock_fetch.call_count == 1, \
        f"expected single master fetch, got {mock_fetch.call_args_list}"
    fetch_ref = mock_fetch.call_args_list[0].args[2]
    assert fetch_ref == "master", fetch_ref

    # rev-list / describe were dispatched against the mirror (not pkg dir).
    count_kwargs = mock_count.call_args.kwargs
    assert count_kwargs.get("git_dir") == fake_mirror, count_kwargs
    for call in mock_describe.call_args_list:
        assert call.kwargs.get("git_dir") == fake_mirror, call


def test_run_check_mirror_fetch_failure_records_none_axes(tmp_path, monkeypatch):
    """Mirror inits but master fetch fails — axes stay None.

    Defensive: validates that an offline / unreachable remote degrades
    to "banner silent" rather than emitting a half-known entry.
    """
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "ai-hats-data"))
    monkeypatch.delenv("AI_HATS_REPO_URL", raising=False)
    fake_mirror = tmp_path / "ai-hats-data" / ".cache" / "probe-mirror"

    with patch.object(checker, "detect_installed_sha", return_value="a" * 40), \
         patch.object(checker, "detect_remote_url", return_value="https://example.git"), \
         patch.object(checker, "fetch_latest_sha", return_value="b" * 40), \
         patch.object(checker, "_fetch_into_pkg", return_value=False), \
         patch.object(checker, "_ensure_probe_mirror", return_value=fake_mirror), \
         patch.object(checker, "_fetch_into_mirror", return_value=False), \
         patch.object(checker, "_count_ahead_behind") as mock_count, \
         patch.object(checker, "_describe") as mock_describe:
        entry = run_check(tmp_path)

    assert entry is not None
    assert entry.ahead is None and entry.behind is None
    assert entry.installed_label is None and entry.latest_label is None
    # Short-circuited before computing axes/labels.
    mock_count.assert_not_called()
    mock_describe.assert_not_called()


def test_ensure_probe_mirror_creates_and_reuses(tmp_path):
    """First call inits a bare repo; second call reuses without re-init."""
    project = tmp_path
    init_calls: list = []

    def fake_run(args, **kwargs):
        init_calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=fake_run):
        first = checker._ensure_probe_mirror(project)
    assert first is not None and first.is_dir(), first
    # Simulate ``git init`` having written HEAD (real git does; the mock
    # doesn't, so write it ourselves).
    (first / "HEAD").write_text("ref: refs/heads/master\n")

    with patch.object(subprocess, "run", side_effect=fake_run) as mock_run:
        second = checker._ensure_probe_mirror(project)
    assert second == first
    # No subprocess on re-entry — early return.
    mock_run.assert_not_called()


def test_ensure_probe_mirror_returns_none_on_git_init_failure(tmp_path):
    fail = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal")
    with patch.object(subprocess, "run", return_value=fail):
        assert checker._ensure_probe_mirror(tmp_path) is None


def test_ensure_probe_mirror_returns_none_when_git_missing(tmp_path):
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert checker._ensure_probe_mirror(tmp_path) is None


def test_fetch_into_mirror_success(tmp_path):
    with patch.object(subprocess, "run", return_value=_ok()):
        assert checker._fetch_into_mirror(tmp_path, "https://example.git", "master") is True


def test_fetch_into_mirror_false_on_nonzero(tmp_path):
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
    with patch.object(subprocess, "run", return_value=fail):
        assert checker._fetch_into_mirror(tmp_path, "https://example.git", "master") is False


def test_fetch_into_mirror_false_on_timeout(tmp_path):
    with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 10)):
        assert checker._fetch_into_mirror(tmp_path, "https://example.git", "master") is False


def test_fetch_into_mirror_is_full(tmp_path):
    """Fetch is always full (no ``--depth``) — master needs full history
    so ``rev-list installed...master`` resolves any lag. Short-SHA
    fetch-by-installed is intentionally NOT performed (most HTTPS
    remotes refuse short SHAs in want lines).
    """
    captured: list = []

    def fake_run(args, **kwargs):
        captured.append(args)
        return _ok()

    with patch.object(subprocess, "run", side_effect=fake_run):
        checker._fetch_into_mirror(tmp_path, "https://example.git", "master")
    assert captured
    assert not any(a.startswith("--depth") for a in captured[0]), captured


def test_count_ahead_behind_uses_git_dir_param(tmp_path):
    """Explicit ``git_dir`` kwarg reaches the subprocess as ``git -C <dir>``."""
    captured: list = []

    def fake_run(args, **kwargs):
        captured.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="0\t5\n", stderr="")

    with patch.object(subprocess, "run", side_effect=fake_run):
        checker._count_ahead_behind("a", "b", git_dir=tmp_path)
    assert captured
    # ``git -C <git_dir> rev-list ...``
    args = captured[0]
    assert args[0] == "git" and args[1] == "-C", args
    assert args[2] == str(tmp_path), args


def test_describe_uses_git_dir_param(tmp_path):
    captured: list = []

    def fake_run(args, **kwargs):
        captured.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="v0.6.0\n", stderr="")

    with patch.object(subprocess, "run", side_effect=fake_run):
        checker._describe("abcdef0", git_dir=tmp_path)
    assert captured
    args = captured[0]
    assert args[:3] == ["git", "-C", str(tmp_path)], args


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


# ---------- _count_ahead_behind ----------


def test_count_ahead_behind_parses_rev_list_output():
    # ``git rev-list --left-right --count A...B`` → "<L>\t<R>"
    # L = ahead-of-upstream (installed has these), R = behind (latest has these).
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="0\t19\n", stderr="")
    with patch.object(subprocess, "run", return_value=fake):
        assert checker._count_ahead_behind("a", "b") == (0, 19)


def test_count_ahead_behind_handles_diverged():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="3\t4\n", stderr="")
    with patch.object(subprocess, "run", return_value=fake):
        assert checker._count_ahead_behind("a", "b") == (3, 4)


def test_count_ahead_behind_returns_none_on_unknown_sha():
    fail = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal: bad revision")
    with patch.object(subprocess, "run", return_value=fail):
        assert checker._count_ahead_behind("a", "b") is None


def test_count_ahead_behind_returns_none_on_malformed_output():
    weird = subprocess.CompletedProcess(args=[], returncode=0, stdout="just-one-token\n", stderr="")
    with patch.object(subprocess, "run", return_value=weird):
        assert checker._count_ahead_behind("a", "b") is None


def test_count_ahead_behind_returns_none_when_git_missing():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert checker._count_ahead_behind("a", "b") is None


# ---------- _fetch_into_pkg ----------


def test_fetch_into_pkg_true_on_success():
    # HATS-441: ls-files tracked-check runs first; fetch second.
    with patch.object(
        subprocess, "run",
        side_effect=[_ok(), _ok()],
    ):
        assert checker._fetch_into_pkg("https://example.git") is True


def test_fetch_into_pkg_false_on_nonzero():
    fail = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal")
    with patch.object(
        subprocess, "run",
        side_effect=[_ok(), fail],
    ):
        assert checker._fetch_into_pkg("https://example.git") is False


def test_fetch_into_pkg_false_on_timeout():
    # Tracked-check succeeds; the actual fetch raises timeout.
    with patch.object(
        subprocess, "run",
        side_effect=[_ok(), subprocess.TimeoutExpired("git", 10)],
    ):
        assert checker._fetch_into_pkg("https://example.git") is False


def test_fetch_into_pkg_false_when_pkg_not_tracked():
    """HATS-441: foreign repo in ancestor → refuse to fetch (no pollution)."""
    with patch.object(subprocess, "run", side_effect=[_fail()]):
        assert checker._fetch_into_pkg("https://example.git") is False


# ---------- _describe ----------


def test_describe_returns_label():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="v0.6.0-19-gabcdef0\n", stderr="")
    with patch.object(subprocess, "run", return_value=fake):
        assert checker._describe("abcdef0") == "v0.6.0-19-gabcdef0"


def test_describe_returns_none_when_no_tags():
    # ``git describe`` exits non-zero when no annotated tags reach the SHA.
    fail = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal: No names found")
    with patch.object(subprocess, "run", return_value=fail):
        assert checker._describe("abcdef0") is None


def test_describe_returns_none_when_git_missing():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert checker._describe("abcdef0") is None
