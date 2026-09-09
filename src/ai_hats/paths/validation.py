"""Value validation for ``paths`` (HATS-831 split).

Holds the validators kept deliberately separate from the path/resolver logic so
callers (``ProjectConfig`` field validators, the builtin-library resolver) can
reference them in one place:

  - :func:`is_library_root` / :func:`_validated_library_root` — root manifest + env gate.
  - :func:`normalize_ai_hats_dir` / :func:`normalize_venv_path` — config-value
    validators used by ``ProjectConfig``.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

from .constants import PIPELINES_SUBPATH, REQUIRED_LIBRARY_LAYERS

# What a root must SERVE, not merely contain: `core/`+`usage/` alone is satisfied
# by a __pycache__ shadow of a half-removed worktree. `hooks/` stays
# out — a source tree without it is legal (callers degrade on None).
_LIBRARY_ROOT_MANIFEST: tuple[tuple[str, ...], ...] = (
    *((layer,) for layer in REQUIRED_LIBRARY_LAYERS),
    PIPELINES_SUBPATH,
)

_MANIFEST_HUMAN = " · ".join("/".join(parts) + "/" for parts in _LIBRARY_ROOT_MANIFEST)


def is_library_root(root: Path) -> bool:
    """Whether ``root`` holds the full builtin-library manifest (HATS-1157).

    THE single answer to "is this a library root?" — the resolver's three entry
    points (env override, source autodetect, and the post-detect gate) all defer
    here, so the rule cannot drift between them again. Callers decide what a
    ``False`` means: the env override rejects loudly, autodetect falls through
    to the installed package.
    """
    return all(root.joinpath(*parts).is_dir() for parts in _LIBRARY_ROOT_MANIFEST)


def _validated_library_root(raw: str | None) -> Path | None:
    """A builtin-library root is valid only if it serves the full manifest.

    A partial root (e.g. ``core`` but no ``usage`` — a corrupt/sparse checkout, or
    a leaked stale ``AI_HATS_LIBRARY_ROOT`` pointing at a half-removed worktree)
    is rejected LOUDLY rather than silently dropping the entire ``usage`` layer.
    Returns the root, or ``None`` (caller falls back to the next resolver).
    """
    if not raw:
        return None
    root = Path(raw).expanduser()
    if is_library_root(root):
        return root
    print(
        f"[ai-hats] AI_HATS_LIBRARY_ROOT={raw!r} does not hold a complete "
        f"builtin library ({_MANIFEST_HUMAN}); "
        "ignoring it and resolving the builtin library normally.",
        file=sys.stderr,
    )
    return None


def normalize_ai_hats_dir(value: str) -> str:
    """Validate + normalize an ``ai_hats_dir`` config value.

    Raises ``ValueError`` on:
      - empty string, ``"."``, ``"/"``
      - absolute paths (project must be relocatable)
      - ``..`` segments (escape out of project)

    Normalization: POSIX-style separators, trailing slash stripped.
    """
    if not value:
        raise ValueError("ai_hats_dir must not be empty")
    p = PurePosixPath(value.replace("\\", "/"))
    if p.is_absolute():
        raise ValueError("ai_hats_dir must be relative to project root (not absolute)")
    if ".." in p.parts:
        raise ValueError("ai_hats_dir must not contain '..' segments")
    s = p.as_posix().rstrip("/")
    if s in {"", ".", "/"}:
        raise ValueError(f"ai_hats_dir is invalid: {value!r}")
    return s


def normalize_venv_path(value: str) -> str:
    """Validate + normalize a ``venv_path`` config value (HATS-334).

    Differs from :func:`normalize_ai_hats_dir` by ALLOWING absolute paths —
    venv may legitimately live outside the project (CI shared cache,
    system-wide ai-hats venv, user-owned override venv).

    Raises ``ValueError`` on:
      - empty string, ``"."``, ``"/"``
      - ``..`` segments (relative escape; not meaningful for absolute either)

    Normalization: POSIX-style separators, trailing slash stripped.
    """
    if not value:
        raise ValueError("venv_path must not be empty")
    p = PurePosixPath(value.replace("\\", "/"))
    if ".." in p.parts:
        raise ValueError("venv_path must not contain '..' segments")
    s = p.as_posix().rstrip("/")
    if s in {"", ".", "/"}:
        raise ValueError(f"venv_path is invalid: {value!r}")
    return s


__all__ = [
    "_validated_library_root",
    "normalize_ai_hats_dir",
    "normalize_venv_path",
]
