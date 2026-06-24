"""HATS-826: builtin library-layer resolution — cwd auto-detect + env override.

Worktree library edits must be visible to in-process composition. A command
whose cwd is inside an ai-hats *source* checkout (including a linked worktree)
must resolve builtin ``core``/``usage`` from THAT checkout, not from the
editable-install main repo that ``importlib.resources`` hard-pins.

Resolution order under test (highest first):
  1. ``AI_HATS_LIBRARY_ROOT`` env override (explicit seam; both-or-none).
  2. cwd auto-detection of an ai-hats source checkout.
  3. ``importlib.resources`` — the installed package (downstream / default).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import (
    _builtin_library_layers,
    _detect_source_library_root,
    _validated_library_root,
)


def _make_source_tree(root: Path) -> Path:
    """Lay out a minimal ai-hats source checkout under ``root``; return root."""
    for layer in ("core", "usage"):
        (root / "library" / layer).mkdir(parents=True)
    (root / "src" / "ai_hats").mkdir(parents=True)
    return root


# ---- _detect_source_library_root -------------------------------------------


def test_detect_finds_source_root_from_nested_cwd(tmp_path):
    _make_source_tree(tmp_path)
    nested = tmp_path / "src" / "ai_hats"
    assert _detect_source_library_root(nested) == tmp_path / "library"


def test_detect_requires_src_ai_hats_not_just_library_core(tmp_path):
    # Downstream repo: a ``library/core`` but NO ``src/ai_hats`` is not a source
    # checkout — must not be mistaken for one (false-positive guard).
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


# ---- _builtin_library_layers precedence ------------------------------------


def test_cwd_autodetect_resolves_worktree_library(tmp_path, monkeypatch):
    _make_source_tree(tmp_path)
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _builtin_library_layers() == [
        tmp_path / "library" / "core",
        tmp_path / "library" / "usage",
    ]


def test_env_override_wins_over_cwd(tmp_path, monkeypatch):
    cwd_tree = _make_source_tree(tmp_path / "cwd")
    env_tree = _make_source_tree(tmp_path / "env")
    monkeypatch.chdir(cwd_tree)
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(env_tree / "library"))
    assert _builtin_library_layers() == [
        env_tree / "library" / "core",
        env_tree / "library" / "usage",
    ]


def test_partial_env_override_falls_back_to_cwd(tmp_path, monkeypatch):
    _make_source_tree(tmp_path)  # valid cwd source tree
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad"
    (bad / "core").mkdir(parents=True)  # core only -> invalid override
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(bad))
    assert _builtin_library_layers() == [
        tmp_path / "library" / "core",
        tmp_path / "library" / "usage",
    ]


def test_downstream_cwd_falls_back_to_importlib(tmp_path, monkeypatch):
    # No ``src/ai_hats`` up-tree -> not a source checkout -> installed package
    # (the real main-repo library in this test env). R2: downstream unaffected.
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    layers = _builtin_library_layers()
    assert layers, "expected importlib fallback to yield the installed library"
    assert all(p.name in ("core", "usage") for p in layers)
    assert tmp_path not in {p.parent.parent for p in layers}
