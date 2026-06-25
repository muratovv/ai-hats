"""Builtin library SOURCE resolution — worktree-aware (HATS-826 / HATS-831).

THE single home for "where is the builtin ``library/`` the engine composes
from?". This is the SHIPPED source (``core``/``usage``/``hooks``/``core/pipelines``),
distinct from :func:`ai_hats.paths.library_dir` (the materialized
``<.agent>/library/`` mirror).

``importlib.resources.files("ai_hats.library")`` hard-pins the editable install
to the MAIN repo regardless of cwd, so library edits made inside a linked
worktree are otherwise invisible to composition (HATS-826). Routing EVERY
consumer (layers, hooks, core pipelines) through these helpers makes
worktree-awareness uniform — and a guard test
(``test_builtin_library_resolver_single_home``) bans the ``files(LIBRARY_PKG)``
call anywhere else, so the resolution cannot silently diverge again (HATS-831).
"""

from __future__ import annotations

import os
from pathlib import Path

from .constants import (
    ENV_LIBRARY_ROOT,
    HOOKS_DIRNAME,
    LIBRARY_LAYERS,
    LIBRARY_PKG,
    PIPELINES_SUBPATH,
)
from .validation import _validated_library_root


def _detect_source_library_root(start: Path) -> Path | None:
    """Walk up from ``start`` for an ai-hats *source* checkout; return its ``library/``.

    A source checkout is a dir holding BOTH ``library/core`` AND ``src/ai_hats``.
    The ``src/ai_hats`` co-requirement is what distinguishes the engine source
    repo (and its linked worktrees) from any downstream project that merely has
    a ``library/core`` of its own — so downstream stays on the installed package
    (HATS-826 R2). Returns ``<dir>/library`` or ``None``.
    """
    for d in (start, *start.parents):
        if (d / "library" / "core").is_dir() and (d / "src" / "ai_hats").is_dir():
            return d / "library"
    return None


def _importlib_library_root() -> Path | None:
    """The installed ``ai_hats.library`` package dir, or ``None`` (broken install).

    File-based setuptools packaging (``package-dir`` maps ``ai_hats.library`` to
    the ``library/`` tree, no ``__init__.py``), so the resource resolves to a
    real filesystem path — no ``as_file`` context manager needed; callers get a
    plain ``Path``.
    """
    from importlib.resources import files

    try:
        root = Path(str(files(LIBRARY_PKG)))
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    return root if root.is_dir() else None


def builtin_library_root() -> Path | None:
    """Resolve the builtin ``library/`` source root (worktree-aware).

    Resolution order (HATS-826), highest precedence first:

    1. ``AI_HATS_LIBRARY_ROOT`` env override — explicit, greppable seam
       (tests, power users), validated both-``core``-and-``usage`` or rejected.
    2. **cwd auto-detection** of an ai-hats source checkout — keys off
       ``Path.cwd()`` so a command run *inside* a worktree resolves THAT
       worktree's ``library/`` (``importlib`` would hard-pin MAIN).
    3. ``importlib.resources`` — the installed package (downstream / default).

    Returns the root dir whose children are ``core``/``usage``/``hooks``/… or
    ``None`` on a broken install. All builtin-library subpaths derive from here.
    """
    root = _validated_library_root(os.environ.get(ENV_LIBRARY_ROOT))
    if root is None:
        root = _detect_source_library_root(Path.cwd())
    if root is not None and all((root / layer).is_dir() for layer in LIBRARY_LAYERS):
        return root
    return _importlib_library_root()


def _importlib_library_layers() -> list[Path]:
    """Resolve ``[core, usage]`` from the installed ``ai_hats.library`` package.

    Falls back to an empty list when the package data is missing (sdist
    inspection in CI / broken install) — callers degrade gracefully.
    """
    root = _importlib_library_root()
    if root is None:
        return []
    return [root / layer for layer in LIBRARY_LAYERS if (root / layer).is_dir()]


def builtin_library_layers() -> list[Path]:
    """The builtin ``[core, usage]`` layers (core first = lowest priority).

    Derived from :func:`builtin_library_root`; both layers must exist under the
    resolved root, else we fall through to the installed package (never a
    partial builtin).
    """
    root = builtin_library_root()
    if root is None:
        return []
    layers = [root / layer for layer in LIBRARY_LAYERS]
    if all(p.is_dir() for p in layers):
        return layers
    return _importlib_library_layers()


def builtin_library_hooks() -> Path | None:
    """The builtin ``library/hooks/`` source dir, or ``None`` if unresolved.

    Callers decide on ``None``: the managed-hook whitelist degrades to empty;
    materialization raises (a broken install is not a state to paper over).
    """
    root = builtin_library_root()
    if root is None:
        return None
    hooks = root / HOOKS_DIRNAME
    return hooks if hooks.is_dir() else None


def core_pipeline_path(name: str) -> Path | None:
    """Filesystem path to a builtin core pipeline YAML, or ``None`` if unresolved.

    Returns a plain ``Path`` (file-based packaging, see
    :func:`_importlib_library_root`) — no ``as_file`` wrapper needed.
    """
    root = builtin_library_root()
    if root is None:
        return None
    return root.joinpath(*PIPELINES_SUBPATH, f"{name}.yaml")


__all__ = [
    "_detect_source_library_root",
    "builtin_library_root",
    "builtin_library_layers",
    "builtin_library_hooks",
    "core_pipeline_path",
]
