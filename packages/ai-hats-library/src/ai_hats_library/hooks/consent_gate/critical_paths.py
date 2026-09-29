"""Which `rm` operands the consent gate asks about: a superset of Claude Code's critical paths (ADR-0039)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

#: An operand the shell expands later cannot be judged now — Claude Code flags these too.
_EXPANSIONS = ("$", "`")
_GLOB = frozenset("*?[")


def critical_target(
    operands: Sequence[str],
    *,
    base: Path | None,
    working: Iterable[Path],
    home: Path,
    extra: Iterable[Path],
) -> str | None:
    """The first operand whose removal is critical, or ``None``.

    ``base`` is where the command runs (``None`` when a `cd` makes it unknowable);
    ``working`` the session's working directories; ``extra`` its additional ones.
    """
    guarded = [*working, home, *extra, *([base] if base is not None else [])]
    anchors = {form for directory in guarded for form in _forms(str(directory))}
    for operand in operands:
        if _critical(operand, base=base, home=home, anchors=anchors):
            return operand
    return None


def _critical(operand: str, *, base: Path | None, home: Path, anchors: set[str]) -> bool:
    if any(mark in operand for mark in _EXPANSIONS):
        return True
    path = operand
    if path == "~" or path.startswith("~/"):
        path = str(home) + path[1:]
    elif path.startswith("~"):
        return True  # `~user` names another home
    if not os.path.isabs(path):
        if base is None:
            return True
        path = os.path.join(base, path)
    return any(_critical_dir(form, anchors) for form in _forms(_static_prefix(path)))


def _static_prefix(path: str) -> str:
    """The directory a glob expands in, or ``path`` itself when it holds no glob."""
    parts = Path(path).parts
    for index, part in enumerate(parts):
        if _GLOB & set(part):
            return str(Path(*parts[:index]))
    return path


def _forms(path: str) -> set[str]:
    """The lexical and the resolved spelling: `/tmp` is top-level, `/private/tmp` is not."""
    return {os.path.normpath(path), os.path.realpath(path)}


def _critical_dir(path: str, anchors: set[str]) -> bool:
    if path == os.sep or os.path.dirname(path) == os.sep:
        return True  # the root, or a top-level directory
    prefix = path.rstrip(os.sep) + os.sep
    return any(anchor == path or anchor.startswith(prefix) for anchor in anchors)
