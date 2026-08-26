"""HATS-826 / HATS-831 / HATS-876: builtin library-layer resolution — cwd
auto-detect + env override + installed-package (as_file) fallback.

Worktree library edits must be visible to in-process composition. A command
whose cwd is inside an ai-hats-library *source* checkout (the monorepo, a linked
worktree, or a standalone git-split of ``ai-hats-library``) must resolve builtin
``core``/``usage`` from THAT checkout, not from the editable-install main repo
that ``importlib.resources`` hard-pins.

Resolution order under test (highest first):
  1. ``AI_HATS_LIBRARY_ROOT`` env override (explicit seam; both-or-none).
  2. cwd auto-detection of an ``ai_hats_library`` source checkout.
  3. ``importlib.resources`` — the installed ``ai_hats_library`` package,
     routed through ``as_file`` so it survives a data-only wheel (T18/P1 #14).
"""

from __future__ import annotations

import warnings

from pathlib import Path

from ai_hats.paths import (
    _detect_source_library_root,
    _validated_library_root,
    builtin_library_hooks,
    builtin_library_layers,
    builtin_library_root,
    core_pipeline_path,
)
from ai_hats.paths import library as libmod
from ai_hats.paths.constants import LIBRARY_LAYERS


def _populate_lib(lib: Path) -> Path:
    """Build the full root manifest a resolver will accept (HATS-1157)."""
    for layer in ("core", "usage"):
        (lib / layer).mkdir(parents=True)
    (lib / "core" / "pipelines").mkdir()
    return lib


def _make_standalone_lib(root: Path) -> Path:
    """A standalone (git-split) ai-hats-library checkout; return its layer-root."""
    return _populate_lib(root / "src" / "ai_hats_library")


def _make_monorepo_lib(root: Path) -> Path:
    """The monorepo/worktree ai-hats-library layout; return its layer-root."""
    return _populate_lib(root / "packages" / "ai-hats-library" / "src" / "ai_hats_library")


# ---- _detect_source_library_root -------------------------------------------


def test_detect_finds_standalone_from_nested_cwd(tmp_path):
    lib = _make_standalone_lib(tmp_path)
    assert _detect_source_library_root(lib / "core") == lib


def test_detect_finds_monorepo_layout(tmp_path):
    lib = _make_monorepo_lib(tmp_path)
    assert _detect_source_library_root(tmp_path) == lib


def test_detect_resolves_library_only_checkout(tmp_path):
    # HATS-876/§6: a standalone library checkout has NO src/ai_hats — the dropped
    # co-requirement means it must still resolve.
    lib = _make_standalone_lib(tmp_path)
    assert not (tmp_path / "src" / "ai_hats").exists()
    assert _detect_source_library_root(tmp_path) == lib


def test_detect_none_for_downstream_project(tmp_path):
    # Downstream repo with its OWN library/core (the old false-positive shape) but
    # no ai_hats_library package -> not a source checkout, stays on the package.
    (tmp_path / "library" / "core").mkdir(parents=True)
    assert _detect_source_library_root(tmp_path) is None


def test_detect_none_when_no_library(tmp_path):
    assert _detect_source_library_root(tmp_path) is None


# ---- _validated_library_root -----------------------------------------------


def test_validated_root_requires_both_core_and_usage(tmp_path, capsys):
    (tmp_path / "core").mkdir()  # usage missing -> partial -> rejected + warned
    assert _validated_library_root(str(tmp_path)) is None
    assert "AI_HATS_LIBRARY_ROOT" in capsys.readouterr().err


def test_validated_root_accepts_complete(tmp_path):
    _populate_lib(tmp_path)
    assert _validated_library_root(str(tmp_path)) == tmp_path


def test_validated_root_none_when_unset():
    assert _validated_library_root(None) is None
    assert _validated_library_root("") is None


# ---- HATS-1157: a root must SERVE the manifest, not merely look like one ----


def _make_pycache_shadow(lib: Path) -> Path:
    """The incident shape: layer dirs survive as __pycache__ homes, content gone."""
    for layer in ("core", "usage"):
        (lib / layer / "__pycache__").mkdir(parents=True)
    return lib


def test_shadow_root_rejected_by_env_override(tmp_path, capsys):
    # Pre-1157 this passed the core/+usage/ check and failed 200 frames later on a
    # missing core/pipelines/*.yaml (the live incident).
    _make_pycache_shadow(tmp_path)
    assert _validated_library_root(str(tmp_path)) is None
    assert "AI_HATS_LIBRARY_ROOT" in capsys.readouterr().err


def test_shadow_root_rejected_by_source_autodetect(tmp_path):
    _make_pycache_shadow(tmp_path / "packages" / "ai-hats-library" / "src" / "ai_hats_library")
    assert _detect_source_library_root(tmp_path) is None


def test_shadow_root_falls_through_to_installed_package_silently(tmp_path, monkeypatch, capsys):
    # Autodetect stays QUIET on rejection: a downstream project legitimately has no
    # source root, so warning here would fire on every launch.
    _make_pycache_shadow(tmp_path / "packages" / "ai-hats-library" / "src" / "ai_hats_library")
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert builtin_library_root() == libmod._importlib_library_root()
    assert capsys.readouterr().err == ""


# ---- builtin_library_layers precedence -------------------------------------


def test_cwd_autodetect_resolves_worktree_library(tmp_path, monkeypatch):
    lib = _make_monorepo_lib(tmp_path)
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert builtin_library_layers() == [lib / "core", lib / "usage"]


def test_env_override_wins_over_cwd(tmp_path, monkeypatch):
    cwd_lib = _make_monorepo_lib(tmp_path / "cwd")
    env_lib = _make_standalone_lib(tmp_path / "env")
    monkeypatch.chdir(cwd_lib.parent)
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(env_lib))
    assert builtin_library_layers() == [env_lib / "core", env_lib / "usage"]


def test_partial_env_override_falls_back_to_cwd(tmp_path, monkeypatch):
    lib = _make_monorepo_lib(tmp_path)
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad"
    (bad / "core").mkdir(parents=True)  # core only -> invalid override
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(bad))
    assert builtin_library_layers() == [lib / "core", lib / "usage"]


def test_downstream_cwd_falls_back_to_importlib(tmp_path, monkeypatch):
    # No ai_hats_library source up-tree -> installed package (the real library in
    # this test env). R2: downstream unaffected.
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    layers = builtin_library_layers()
    assert layers, "expected importlib fallback to yield the installed library"
    assert all(p.name in LIBRARY_LAYERS for p in layers)
    assert tmp_path not in {p.parent.parent for p in layers}


# ---- builtin_library_root + subpath accessors (HATS-831) -------------------


def test_root_subpaths_derive_from_resolved_root(tmp_path, monkeypatch):
    # All builtin subpaths must derive from the SAME resolved root — proving the
    # single-source-of-truth refactor (hooks + core pipelines follow the same
    # cwd/env signal as the composition layers).
    lib = _make_monorepo_lib(tmp_path)
    (lib / "hooks").mkdir()
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert builtin_library_root() == lib
    assert builtin_library_hooks() == lib / "hooks"
    assert core_pipeline_path("execute") == lib / "core" / "pipelines" / "execute.yaml"


def test_builtin_library_hooks_none_when_hooks_dir_absent(tmp_path, monkeypatch):
    # Source tree without a hooks/ dir -> resolver returns None (callers decide:
    # whitelist degrades to empty, materialize raises).
    _make_monorepo_lib(tmp_path)
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert builtin_library_hooks() is None


# ---- _importlib_library_root — the as_file seam (T18/HATS-876) --------------


def test_importlib_root_resolves_installed_package():
    # The installed ai_hats_library resolves to a real dir through the as_file
    # seam (a no-op passthrough for the real-dir install we ship).
    root = libmod._importlib_library_root()
    assert root is not None
    assert (root / "core").is_dir() and (root / "usage").is_dir()


def test_importlib_root_none_when_package_missing(monkeypatch):
    # files() raising (broken / absent package) -> None, not a crash. cache_clear
    # brackets the monkeypatch so the lru_cache never poisons other tests.
    def _miss(_pkg):
        raise ModuleNotFoundError("no ai_hats_library")

    libmod._importlib_library_root.cache_clear()
    monkeypatch.setattr(libmod, "files", _miss)
    try:
        assert libmod._importlib_library_root() is None
    finally:
        libmod._importlib_library_root.cache_clear()


# ---- project_dir / AI_HATS_PROJECT_DIR precedence over cwd (HATS-1127) -----


def test_project_dir_parameter_wins_over_cwd(tmp_path, monkeypatch):
    cwd_lib = _make_monorepo_lib(tmp_path / "cwd_repo")
    proj_lib = _make_monorepo_lib(tmp_path / "proj_repo")
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(cwd_lib.parent)

    assert builtin_library_root(tmp_path / "proj_repo") == proj_lib
    assert builtin_library_layers(tmp_path / "proj_repo") == [proj_lib / "core", proj_lib / "usage"]


def test_ai_hats_project_dir_env_wins_over_cwd(tmp_path, monkeypatch):
    cwd_lib = _make_monorepo_lib(tmp_path / "cwd_repo")
    proj_lib = _make_monorepo_lib(tmp_path / "proj_repo")
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(cwd_lib.parent)
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(tmp_path / "proj_repo"))

    assert builtin_library_root() == proj_lib


# ---- HATS-1508: user-global library_paths.yaml ------------------------------


def test_user_global_library_paths_loaded(tmp_path, monkeypatch):
    """HATS-1508: ~/.ai-hats/library_paths.yaml specifies extra user-global library paths."""
    from ai_hats.library_paths import build_library_paths

    user_home_dir = tmp_path / "user_home"
    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir(parents=True)

    ext_lib = tmp_path / "external_lib"
    ext_lib.mkdir()

    (ai_hats_dir / "library_paths.yaml").write_text(f"paths:\n  - {ext_lib}\n")

    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    paths = build_library_paths(tmp_path / "project")
    assert ext_lib in paths


def test_user_global_library_paths_nonexistent_directory_warns(tmp_path, monkeypatch, caplog):
    """HATS-1508: Non-existent directory in library_paths.yaml logs a warning and is skipped."""
    import logging
    from ai_hats.library_paths import build_library_paths

    user_home_dir = tmp_path / "user_home"
    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir(parents=True)

    non_existent = tmp_path / "does_not_exist"
    (ai_hats_dir / "library_paths.yaml").write_text(f"paths:\n  - {non_existent}\n")

    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    with caplog.at_level(logging.WARNING):
        paths = build_library_paths(tmp_path / "project")

    assert non_existent not in paths
    assert "user library paths: directory does not exist" in caplog.text
    assert str(non_existent) in caplog.text


def test_user_global_library_paths_malformed_yaml_warns(tmp_path, monkeypatch, caplog):
    """HATS-1508: Malformed YAML in library_paths.yaml logs a warning and returns empty."""
    import logging
    from ai_hats.library_paths import build_library_paths

    user_home_dir = tmp_path / "user_home"
    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir(parents=True)

    (ai_hats_dir / "library_paths.yaml").write_text("paths: [unclosed list")

    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    with caplog.at_level(logging.WARNING):
        build_library_paths(tmp_path / "project")

    assert "user library paths: failed to load" in caplog.text


def test_user_global_library_paths_missing_file_silent(tmp_path, monkeypatch, caplog):
    """HATS-1508: Missing library_paths.yaml returns empty silently with no warnings."""
    import logging
    from ai_hats.library_paths import build_library_paths

    user_home_dir = tmp_path / "user_home"
    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir(parents=True)

    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    with caplog.at_level(logging.WARNING):
        build_library_paths(tmp_path / "project")

    assert "user library paths" not in caplog.text


def test_layer_precedence_project_overrides_user_global(tmp_path, monkeypatch):
    """HATS-1508: Precedence order: project config_paths override user-global library_paths.yaml."""
    from ai_hats.library_paths import build_library_paths

    user_home_dir = tmp_path / "user_home"
    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir(parents=True)

    user_ext_lib = tmp_path / "user_ext_lib"
    user_ext_lib.mkdir()
    (ai_hats_dir / "library_paths.yaml").write_text(f"paths:\n  - {user_ext_lib}\n")

    proj_cfg_lib = tmp_path / "proj_cfg_lib"
    proj_cfg_lib.mkdir()

    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    paths = build_library_paths(tmp_path / "project", config_paths=[proj_cfg_lib])

    user_idx = paths.index(user_ext_lib)
    proj_idx = paths.index(proj_cfg_lib)
    assert user_idx < proj_idx, "User-global paths must rank lower than project config_paths"


def test_skill_search_roots_and_assembler_parity(tmp_path, monkeypatch):
    """HATS-1508 / Verification #3: _skill_search_roots (wt_lifecycle) and
    build_library_paths return identical lists when given the same inputs."""
    from ai_hats.library_paths import build_library_paths
    from ai_hats.wt_lifecycle import _skill_search_roots

    user_home_dir = tmp_path / "user_home"
    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir(parents=True)

    user_ext_lib = tmp_path / "user_ext_lib"
    user_ext_lib.mkdir()
    (ai_hats_dir / "library_paths.yaml").write_text(f"paths:\n  - {user_ext_lib}\n")

    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    paths_direct = build_library_paths(project_dir)
    paths_wt = _skill_search_roots(project_dir, worktree_path=None)

    assert paths_direct == paths_wt


# ---- prefer_cwd: read-only composition follows cwd (HATS-1501) --------------


def _fake_worktree(main_repo: Path, wt: Path) -> None:
    """Link ``wt`` to ``main_repo`` the way ``git worktree add`` does."""
    (main_repo / ".git" / "worktrees" / wt.name).mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main_repo / '.git' / 'worktrees' / wt.name}\n")


def test_prefer_cwd_resolves_worktree_library_over_project(tmp_path, monkeypatch):
    """The false green: project_dir points at main, the edit lives in the worktree."""
    main_lib = _make_monorepo_lib(tmp_path / "main")
    wt_lib = _make_monorepo_lib(tmp_path / "wt")
    _fake_worktree(tmp_path / "main", tmp_path / "wt")
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    here = tmp_path / "wt"

    assert builtin_library_root(tmp_path / "main", prefer_cwd=True, cwd=here) == wt_lib
    assert builtin_library_root(tmp_path / "main", cwd=here) == main_lib


def test_prefer_cwd_silent_for_sibling_worktree(tmp_path, monkeypatch):
    """A worktree diverges from its main checkout by construction — not news."""
    _make_monorepo_lib(tmp_path / "main")
    _make_monorepo_lib(tmp_path / "wt")
    _fake_worktree(tmp_path / "main", tmp_path / "wt")
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    libmod._warn_library_divergence.cache_clear()
    libmod._git_common_dir.cache_clear()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        builtin_library_root(tmp_path / "main", prefer_cwd=True, cwd=tmp_path / "wt")

    assert [str(w.message) for w in caught] == []


def test_warns_when_cwd_checkout_shadows_unrelated_project(tmp_path, monkeypatch):
    """Two unrelated checkouts: cwd's library is NOT the one the project meant."""
    _make_monorepo_lib(tmp_path / "one")
    _make_monorepo_lib(tmp_path / "two")
    (tmp_path / "one" / ".git").mkdir()
    (tmp_path / "two" / ".git").mkdir()
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    libmod._warn_library_divergence.cache_clear()
    libmod._git_common_dir.cache_clear()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        builtin_library_root(tmp_path / "two", prefer_cwd=True, cwd=tmp_path / "one")

    assert len(caught) == 1
    assert "HATS-1501" in str(caught[0].message)


def test_no_warn_when_no_project_named(tmp_path, monkeypatch):
    """cwd as the only signal is the HATS-826 fallback, not a divergence."""
    _make_monorepo_lib(tmp_path / "solo")
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)
    libmod._warn_library_divergence.cache_clear()
    libmod._git_common_dir.cache_clear()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        builtin_library_root(cwd=tmp_path / "solo")

    assert [str(w.message) for w in caught] == []
