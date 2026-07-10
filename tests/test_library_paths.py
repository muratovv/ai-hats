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


def _make_standalone_lib(root: Path) -> Path:
    """A standalone (git-split) ai-hats-library checkout; return its layer-root."""
    lib = root / "src" / "ai_hats_library"
    for layer in ("core", "usage"):
        (lib / layer).mkdir(parents=True)
    return lib


def _make_monorepo_lib(root: Path) -> Path:
    """The monorepo/worktree ai-hats-library layout; return its layer-root."""
    lib = root / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
    for layer in ("core", "usage"):
        (lib / layer).mkdir(parents=True)
    return lib


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
    (tmp_path / "core").mkdir()
    (tmp_path / "usage").mkdir()
    assert _validated_library_root(str(tmp_path)) == tmp_path


def test_validated_root_none_when_unset():
    assert _validated_library_root(None) is None
    assert _validated_library_root("") is None


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
    assert all(p.name in ("core", "usage") for p in layers)
    assert tmp_path not in {p.parent.parent for p in layers}


# ---- builtin_library_root + subpath accessors (HATS-831) -------------------


def test_root_subpaths_derive_from_resolved_root(tmp_path, monkeypatch):
    # All builtin subpaths must derive from the SAME resolved root — proving the
    # single-source-of-truth refactor (hooks + core pipelines follow the same
    # cwd/env signal as the composition layers).
    lib = _make_monorepo_lib(tmp_path)
    (lib / "hooks").mkdir()
    (lib / "core" / "pipelines").mkdir(parents=True)
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
