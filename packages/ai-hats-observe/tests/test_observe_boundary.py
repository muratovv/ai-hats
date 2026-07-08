"""Workspace-boundary import-lint for the standalone ``ai_hats_observe`` package.

ADR-0014 Phase 1 (HATS-948). Every ``ai_hats_observe`` import must resolve to
stdlib, ``ai_hats_core``, or intra-package — NEVER the ``ai_hats`` integrator
(one-directional: ai_hats -> ai_hats_observe, never back). That is what lets the
package install + run standalone (runtime proof: ``test_observe_standalone``);
this is the static guard, checked at every import level (deferred/TYPE_CHECKING
included).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PKG = "ai_hats_observe"
SRC = Path(__file__).resolve().parent.parent / "src" / PKG

# The observe core is core-only: no third-party deps beyond ai_hats_core.
_ALLOWED_FIRST_PARTY = {"ai_hats_core"}


def _top_level_import_roots(tree: ast.Module) -> set[str]:
    """Top-level package name of every absolute import anywhere in the tree.

    Full walk (module-level, deferred, and ``TYPE_CHECKING`` alike). Relative
    imports are intra-package by construction, so they are skipped.
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
        or root in sys.stdlib_module_names
    )


def test_observe_imports_only_core_and_stdlib() -> None:
    """``ai_hats_observe`` imports only stdlib / ai_hats_core / intra-package —
    never ``ai_hats``. RED-under-revert: a re-introduced eager
    ``ai_hats.environment_recovery`` (or any ``ai_hats.*``) import fails here."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        roots = _top_level_import_roots(ast.parse(path.read_text()))
        bad = sorted(r for r in roots if not _is_allowed(r))
        if bad:
            offenders[str(path.relative_to(SRC))] = bad
    assert not offenders, (
        "ai_hats_observe must import only stdlib + ai_hats_core + intra-package "
        f"(never ai_hats): {offenders}"
    )


def test_boundary_lint_self_test() -> None:
    """The detector FIRES on a synthetic integrator import and stays quiet on the
    allowed set — a green gate means 'boundary clean', not 'detector broken'."""
    forbidden = _top_level_import_roots(
        ast.parse("import ai_hats\nfrom ai_hats.paths import runs_dir")
    )
    assert any(not _is_allowed(r) for r in forbidden)

    allowed = _top_level_import_roots(
        ast.parse(
            "import os\n"
            "from ai_hats_core import atomic_write_text\n"
            "from .session import Session\n"
        )
    )
    assert all(_is_allowed(r) for r in allowed)
