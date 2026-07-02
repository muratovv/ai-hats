"""Workspace-boundary import-lint for the standalone ``ai_hats_wt`` package.

ADR-0013 D6 + HATS-882 (W4). Every ``ai_hats_wt`` import must resolve to stdlib,
``filelock``, ``ai_hats_core``, or intra-package — NEVER the ``ai_hats``
integrator (one-directional: ai_hats -> ai_hats_wt, never back). That allowlist
is what lets the package install + run standalone (runtime proof:
``test_wt_standalone``); this is the static, dependency-free guard.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PKG = "ai_hats_wt"
SRC = Path(__file__).resolve().parent.parent / "src" / PKG

# The package's only non-stdlib deps (pyproject ``dependencies``): the ai-hats
# core primitives and the filelock backend. Anything else — above all the
# ``ai_hats`` integrator — is a boundary violation.
_ALLOWED_FIRST_PARTY = {"ai_hats_core"}
_ALLOWED_THIRD_PARTY = {"filelock"}


def _top_level_import_roots(tree: ast.Module) -> set[str]:
    """Top-level package name of every absolute import anywhere in the tree.

    Full walk (module-level, deferred, and ``TYPE_CHECKING`` alike) — the
    boundary holds at every level. Relative imports (``from . import ...``) are
    intra-package by construction, so they are skipped.
    """
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _is_allowed(root: str) -> bool:
    return (
        root == PKG
        or root in _ALLOWED_FIRST_PARTY
        or root in _ALLOWED_THIRD_PARTY
        or root in sys.stdlib_module_names
    )


def test_ai_hats_wt_imports_only_declared_deps():
    """ai_hats_wt imports only stdlib / filelock / ai_hats_core / intra-package."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        roots = _top_level_import_roots(ast.parse(path.read_text()))
        bad = sorted(r for r in roots if not _is_allowed(r))
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "ai_hats_wt must import only stdlib + filelock + ai_hats_core "
        f"(never the ai_hats integrator): {offenders}"
    )


def test_boundary_lint_self_test():
    """The detector FIRES on a synthetic integrator import and stays quiet on the
    allowed set — so a green gate means 'boundary clean', not 'detector broken'."""
    forbidden = _top_level_import_roots(
        ast.parse("import ai_hats\nfrom ai_hats.models import ComponentConfig")
    )
    assert any(not _is_allowed(r) for r in forbidden)

    allowed = _top_level_import_roots(
        ast.parse(
            "import os\n"
            "import filelock\n"
            "from ai_hats_core import atomic_write_text\n"
            "from .locks import _state_key\n"  # relative -> intra-package, skipped
        )
    )
    assert allowed and all(_is_allowed(r) for r in allowed)
