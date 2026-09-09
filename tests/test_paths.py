"""What stays in the ``paths`` leaf once the layout owns the geometry (ADR-0026)."""

from __future__ import annotations

import os

import pytest

from ai_hats.paths import (
    LEGACY_PATH_MAP,
    legacy_paths_by_class,
    normalize_ai_hats_dir,
    normalize_venv_path,
    user_home,
)
from ai_hats.version_refs import _is_safe_sha_component
from ai_hats_core.layout import ProjectLayout, cache_home, project_key

# ---------- legacy migration helpers ----------


def _seed_legacy_layout(project_dir):
    """Create every legacy path in LEGACY_PATH_MAP so detection sees them all."""
    for legacy in LEGACY_PATH_MAP:
        target = project_dir / legacy
        target.parent.mkdir(parents=True, exist_ok=True)
        if legacy.endswith(".md") or legacy.endswith(".json") or legacy.endswith(".last_backup"):
            target.write_text("seed")
        else:
            target.mkdir(parents=True, exist_ok=True)


def test_legacy_paths_by_class_filters(tmp_path):
    _seed_legacy_layout(tmp_path)
    layout = ProjectLayout.at(tmp_path)
    sessions = legacy_paths_by_class(layout, "sessions")
    tracker = legacy_paths_by_class(layout, "tracker")
    library = legacy_paths_by_class(layout, "library")
    root = legacy_paths_by_class(layout, "root")
    all_pairs = sessions + tracker + library + root
    # Union across classes reproduces the full map, with no overlap.
    assert len(all_pairs) == len(LEGACY_PATH_MAP)
    all_olds = {p[0] for p in all_pairs}
    assert len(all_olds) == len(LEGACY_PATH_MAP)
    # Sanity: every old path is absolute and exists; every new path is under the base.
    for old, new in all_pairs:
        assert old.is_absolute() and old.exists()
        assert new.is_relative_to(layout.base)


def test_legacy_paths_by_class_empty_project(tmp_path):
    for class_ in ("sessions", "tracker", "library", "root"):
        assert legacy_paths_by_class(ProjectLayout.at(tmp_path), class_) == []


# ---------- HATS-316: normalize_ai_hats_dir validation ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (".agent/ai-hats", ".agent/ai-hats"),
        (".agent/ai-hats/", ".agent/ai-hats"),
        ("custom-dir", "custom-dir"),
        ("nested/path/here", "nested/path/here"),
    ],
)
def test_normalize_ai_hats_dir_accepts(raw, expected):
    assert normalize_ai_hats_dir(raw) == expected


@pytest.mark.parametrize(
    "bad",
    ["", ".", "/", "/abs/path", "../escape", "a/../b"],
)
def test_normalize_ai_hats_dir_rejects(bad):
    with pytest.raises(ValueError):
        normalize_ai_hats_dir(bad)


# ---------- HATS-334: venv_path validation ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (".venv", ".venv"),
        ("custom/venv", "custom/venv"),
        ("/opt/myvenv", "/opt/myvenv"),
        ("/opt/myvenv/", "/opt/myvenv"),
    ],
)
def test_normalize_venv_path_accepts(raw, expected):
    """venv_path allows both relative and absolute (unlike ai_hats_dir)."""
    assert normalize_venv_path(raw) == expected


@pytest.mark.parametrize(
    "bad",
    ["", ".", "/", "../escape", "a/../b"],
)
def test_normalize_venv_path_rejects(bad):
    """venv_path rejects empty / dot / dotdot just like ai_hats_dir,
    but absolute is OK."""
    with pytest.raises(ValueError):
        normalize_venv_path(bad)


def test_normalize_venv_path_allows_absolute_unlike_ai_hats_dir():
    """Pin the deliberate divergence from normalize_ai_hats_dir."""
    assert normalize_venv_path("/opt/venv") == "/opt/venv"
    with pytest.raises(ValueError):
        normalize_ai_hats_dir("/opt/venv")


# ---------- user_home (HATS-532) ----------


def test_user_home_default(monkeypatch):
    """Env unset → falls through to ``Path.home()``."""
    from pathlib import Path

    monkeypatch.delenv("AI_HATS_USER_HOME", raising=False)
    assert user_home() == Path.home()


def test_user_home_env_override(tmp_path, monkeypatch):
    """AI_HATS_USER_HOME points the resolver at an isolated dir.

    Sanity for HATS-532's primary motivation: e2e tests can isolate
    ``~/.ai-hats/`` without touching ``HOME`` (which breaks claude
    auth on macOS).
    """
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path))
    assert user_home() == tmp_path


def test_user_home_env_expands_user(tmp_path, monkeypatch):
    """AI_HATS_USER_HOME with leading ``~`` gets expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AI_HATS_USER_HOME", "~/fake-home")
    assert user_home() == tmp_path / "fake-home"


def test_user_home_env_empty_string_falls_back(monkeypatch):
    """Empty ``AI_HATS_USER_HOME`` is treated as unset."""
    from pathlib import Path

    monkeypatch.setenv("AI_HATS_USER_HOME", "")
    assert user_home() == Path.home()


# ---------- cache class, out of the workspace (HATS-1398) ----------


@pytest.fixture
def _no_cache_env(monkeypatch):
    for var in ("AI_HATS_CACHE_HOME", "XDG_CACHE_HOME", "AI_HATS_USER_HOME"):
        monkeypatch.delenv(var, raising=False)


def test_cache_home_precedence_ai_hats_var_wins(tmp_path, monkeypatch, _no_cache_env):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "explicit"))
    assert cache_home(os.environ) == tmp_path / "explicit"


def test_cache_home_falls_back_to_xdg(tmp_path, monkeypatch, _no_cache_env):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_home(os.environ) == tmp_path / "xdg" / "ai-hats"


def test_cache_home_bottoms_out_on_user_home(tmp_path, monkeypatch, _no_cache_env):
    """Bottom of the chain is the ai-hats user home, so AI_HATS_USER_HOME isolates e2e."""
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path))
    assert cache_home(os.environ) == tmp_path / ".cache" / "ai-hats"


def test_cache_home_expands_user(tmp_path, monkeypatch, _no_cache_env):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AI_HATS_CACHE_HOME", "~/cache")
    assert cache_home(os.environ) == tmp_path / "cache"


def test_project_key_separates_same_basename_projects(tmp_path):
    """Two checkouts sharing a dirname must not share a cache root."""
    a = tmp_path / "a" / "proj"
    b = tmp_path / "b" / "proj"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert project_key(a) != project_key(b)
    assert project_key(a).startswith("proj-")
    assert project_key(b).startswith("proj-")


def test_project_key_normalizes_dotdot_and_symlinks(tmp_path):
    """A path spelled differently is the same project — resolve before hashing."""
    real = tmp_path / "proj"
    real.mkdir()
    spelled = tmp_path / "proj" / ".." / "proj"
    link = tmp_path / "link"
    link.symlink_to(real)
    assert project_key(spelled) == project_key(real)
    assert project_key(link) == project_key(real)


def test_project_key_sanitizes_unsafe_dirname(tmp_path):
    """The slug is a single safe path component, whatever the dirname holds."""
    weird = tmp_path / "my proj:v2"
    weird.mkdir()
    key = project_key(weird)
    assert "/" not in key and " " not in key and ":" not in key
    assert _is_safe_sha_component(key)


def test_cache_root_appends_project_key_to_any_base(tmp_path, monkeypatch, _no_cache_env):
    """The per-project segment is unconditional — a leaked var cannot merge caches."""
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "base"))
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert ProjectLayout.compute(a, os.environ).cache.root.parent == tmp_path / "base"
    assert (
        ProjectLayout.compute(a, os.environ).cache.root
        != ProjectLayout.compute(b, os.environ).cache.root
    )


def test_worktree_checkouts_dir_sits_under_the_project_cache_root(
    tmp_path, monkeypatch, _no_cache_env
):
    """Worktrees are minted under the cache root, never in the temp root (HATS-1632).

    macOS reaps ``$TMPDIR`` by access time, and a uv-materialized venv inherits the
    cache's atime — so a worktree born there is already past the threshold.
    """
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "base"))
    project = tmp_path / "proj"
    project.mkdir()
    layout = ProjectLayout.compute(project, os.environ)

    root = layout.cache.worktree_checkouts

    assert root.parent == layout.cache.root
    assert not root.is_relative_to(project)
    # The metadata dir is a different thing under a different root — same word,
    # different meaning, which is exactly why the names differ.
    assert root != layout.sessions.worktrees


def test_cache_class_lives_outside_the_project(tmp_path, monkeypatch, _no_cache_env):
    """R16: no member of the cache class resolves inside the workspace."""
    from ai_hats.update_check.cache import cache_path

    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "base"))
    project = tmp_path / "proj"
    (project / ".agent" / "ai-hats").mkdir(parents=True)
    layout = ProjectLayout.at(project)

    for path in (
        layout.cache.session("sid-1"),
        layout.cache.sessions,
        layout.cache.root / "probe-mirror",
        cache_path(layout.cache),
    ):
        assert not path.is_relative_to(project), path


# HATS-1006: user-global Claude settings resolution


def test_claude_user_settings_json_defaults_to_home(monkeypatch, tmp_path):
    from ai_hats.paths.claude import claude_user_settings_json

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("ai_hats.paths.claude.Path.home", lambda: tmp_path)
    assert claude_user_settings_json() == tmp_path / ".claude" / "settings.json"


def test_claude_user_settings_json_honors_claude_config_dir(monkeypatch, tmp_path):
    from ai_hats.paths.claude import claude_user_settings_json

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert claude_user_settings_json() == tmp_path / "cfg" / "settings.json"
