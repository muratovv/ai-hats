"""Value validation for ``paths`` (HATS-831 split).

Holds the validators kept deliberately separate from the path/resolver logic so
callers (``ProjectConfig`` field validators, the builtin-library resolver) can
reference them in one place:

  - :func:`_validated_library_root` — builtin-library root both-or-none gate.
  - :func:`normalize_ai_hats_dir` / :func:`normalize_venv_path` — config-value
    validators used by ``ProjectConfig``.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

from .constants import LIBRARY_LAYERS


def _validated_library_root(raw: str | None) -> Path | None:
    """A builtin-library root is valid only if it holds BOTH ``core`` and ``usage``.

    A partial root (e.g. ``core`` but no ``usage`` — a corrupt/sparse checkout, or
    a leaked stale ``AI_HATS_LIBRARY_ROOT`` pointing at a half-removed worktree)
    is rejected LOUDLY rather than silently dropping the entire ``usage`` layer.
    Returns the root, or ``None`` (caller falls back to the next resolver).
    """
    if not raw:
        return None
    root = Path(raw).expanduser()
    if all((root / layer).is_dir() for layer in LIBRARY_LAYERS):
        return root
    print(
        f"[ai-hats] AI_HATS_LIBRARY_ROOT={raw!r} has no core/+usage/ pair; "
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
