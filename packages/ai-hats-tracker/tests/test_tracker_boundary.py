"""Workspace-boundary import-lint for the standalone ``ai_hats_tracker`` package.

ADR-0014 Phase 2 (HATS-933, HATS-934). Every ``ai_hats_tracker`` import must
resolve to stdlib, ``pydantic``/``yaml``/``filelock``/``click``/``rich``,
``ai_hats_core``, or intra-package — NEVER the ``ai_hats`` integrator (one-
directional: ai_hats -> ai_hats_tracker, never back). ``ai_hats_wt`` is a soft
optional extra tolerated ONLY in the ``cli/`` subpackage; the schema/FSM core
stays wt-free. That allowlist is what lets the package install + run standalone
(runtime proof: ``test_tracker_standalone``); this is the static guard.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PKG = "ai_hats_tracker"
SRC = Path(__file__).resolve().parent.parent / "src" / PKG

# Non-stdlib deps (pyproject ``dependencies``): ai-hats-core, pydantic/yaml
# (schema), filelock (FSM lock), click/rich (backlog CLI — HATS-934). Anything
# else — above all ``ai_hats`` / ``ai_hats_wt`` — is a boundary violation.
_ALLOWED_FIRST_PARTY = {"ai_hats_core"}
_ALLOWED_THIRD_PARTY = {"pydantic", "yaml", "filelock", "click", "rich"}


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


def test_ai_hats_tracker_imports_only_declared_deps():
    """ai_hats_tracker imports only stdlib / pydantic / yaml / filelock / click /
    rich / ai_hats_core / intra-package — never ai_hats. ``ai_hats_wt`` is a soft
    optional extra permitted ONLY in the ``cli/`` subpackage (HATS-934); the
    FSM/schema core must never touch it. Runtime wt-free proof: the CLI standalone
    test asserts ``import ai_hats_tracker.cli.task`` pulls no ``ai_hats_wt``."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        roots = _top_level_import_roots(ast.parse(path.read_text()))
        # ai_hats_wt is an optional extra, soft-imported by the backlog CLI only.
        allowed_extra = {"ai_hats_wt"} if path.parent.name == "cli" else set()
        bad = sorted(r for r in roots if not _is_allowed(r) and r not in allowed_extra)
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "ai_hats_tracker must import only stdlib + pydantic + yaml + filelock + "
        f"click + rich + ai_hats_core (ai_hats_wt soft in cli/ only; never "
        f"ai_hats): {offenders}"
    )


def test_boundary_lint_self_test():
    """The detector FIRES on a synthetic integrator/wt import and stays quiet on
    the allowed set — so a green gate means 'boundary clean', not 'detector
    broken'."""
    forbidden = _top_level_import_roots(
        ast.parse("import ai_hats\nfrom ai_hats.state import TaskManager\nimport ai_hats_wt")
    )
    assert {r for r in forbidden if not _is_allowed(r)} == {"ai_hats", "ai_hats_wt"}

    allowed = _top_level_import_roots(
        ast.parse(
            "import os\n"
            "import yaml\n"
            "from pydantic import Field\n"
            "from filelock import FileLock\n"
            "from ai_hats_core import atomic_write_text\n"
            "from .models import TaskCard\n"  # relative -> intra-package, skipped
        )
    )
    assert allowed and all(_is_allowed(r) for r in allowed)


def test_boundary_covers_cli_subpackage():
    """The lint walks the ``cli/`` backlog-CLI modules (HATS-934), not just the
    schema/FSM core — else a wt-optional CLI file could smuggle a forbidden
    ``ai_hats`` import past the gate."""
    scanned = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")}
    assert "cli/task.py" in scanned, scanned
    assert "cli/attach.py" in scanned, scanned
