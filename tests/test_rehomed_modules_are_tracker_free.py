"""HATS-1258: the re-homed modules and the rack path import no ``ai_hats_tracker``.

S5 (HATS-1262) deletes the package. A module listed here that reaches back into
it would break on *that* card, silently, so the boundary is pinned on this one.
AST-level like the tracker's own ``test_tracker_boundary`` — a deferred or
``TYPE_CHECKING`` import must not hide from the check.

The rest of ``src/ai_hats`` still imports the tracker on purpose: ``retro/window``
(S2), ``cli/`` (S3), ``models`` (S5). This guard grows as those cards land.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "ai_hats"

FORBIDDEN = "ai_hats_tracker"

#: Re-homed by HATS-1258, plus the two rack-path modules whose consumers were
#: flipped onto them — together, everything the modern path needs.
GUARDED = (
    "ownership.py",
    "linked_context.py",
    "tracker_wiring.py",
    "rack_wiring.py",
    "subagent_runner.py",
)


def _import_roots(tree: ast.Module) -> set[str]:
    """Top-level package of every absolute import anywhere in the tree — module
    level, deferred, and ``TYPE_CHECKING`` alike. Relative imports are
    intra-package by construction and skipped."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_guarded_modules_import_no_tracker():
    """Every guarded module stands on its own once the package is deleted."""
    missing = [name for name in GUARDED if not (SRC / name).is_file()]
    assert not missing, f"guarded module missing (renamed? deleted?): {missing}"

    offenders = [
        name for name in GUARDED if FORBIDDEN in _import_roots(ast.parse((SRC / name).read_text()))
    ]
    assert not offenders, (
        f"these modules must not import {FORBIDDEN} — it is deleted by HATS-1262: {offenders}"
    )


def test_detector_fires_on_a_tracker_import():
    """A green gate means 'boundary clean', not 'detector broken'."""
    assert FORBIDDEN in _import_roots(ast.parse("from ai_hats_tracker import ownership"))
    assert FORBIDDEN in _import_roots(ast.parse("import ai_hats_tracker.state"))
    # deferred, inside a function body — the walk must still see it
    assert FORBIDDEN in _import_roots(
        ast.parse("def f():\n    from ai_hats_tracker.linked_context import load_ticket\n")
    )
    assert FORBIDDEN not in _import_roots(
        ast.parse("from . import ownership\nfrom ai_hats_rack.models import TaskCard\n")
    )
