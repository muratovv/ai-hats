"""HATS-1826 — every first-party ``from X import name`` names something X really has.

The gate ``test_import_hygiene.py`` reads the same imports and is deliberately blind to
this: its subject is runtime cycles, so it declares ``TYPE_CHECKING`` blocks and deferred
imports out of scope. That blindness is correct there and total here — the class this file
catches lives almost entirely inside those blocks, because that is where an annotation-only
import goes.

The incident: HATS-1826 moved the surface contract out of the registry module into
``ai_hats.surfaces.contract``, and three surfaces kept importing ``CompositionResult`` from
the module that no longer had it. Nothing went red. ``from __future__ import annotations``
means the annotation is never evaluated, ``TYPE_CHECKING`` means the import never runs, and
the repository ships no type checker — so no gate in the tree could observe it.

Resolution is by import, not by reading the target's AST: a name a package re-exports, or
binds in a way only execution settles, is a real name, and a static reader would have to
re-implement the import system to agree. The modules here are first-party and the suite
imports them anyway.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# The distributions that ship from this repository. A dangling name in someone else's
# package is their bug and not ours to gate; a dangling name in ours is always ours.
FIRST_PARTY_ROOTS = (
    "ai_hats",
    "ai_hats_core",
    "ai_hats_observe",
    "ai_hats_wt",
    "ai_hats_rack",
    "ai_hats_library",
)


def _is_first_party(module: str) -> bool:
    return any(module == root or module.startswith(f"{root}.") for root in FIRST_PARTY_ROOTS)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _absolute_target(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    """The module an ``ImportFrom`` names, with relative levels resolved against ``module``."""
    if not node.level:
        return node.module or ""
    base = module if is_package else module.rsplit(".", 1)[0]
    parts = base.split(".")
    parts = parts[: len(parts) - (node.level - 1)]
    return ".".join(parts + ([node.module] if node.module else []))


def _dangling_names() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _absolute_target(node, module, path.name == "__init__.py")
            if not _is_first_party(target):
                continue
            try:
                imported = importlib.import_module(target)
            except ImportError:
                # An optional dependency the module itself guards. Not this gate's
                # subject: it would report the absent extra, not a misspelled name.
                continue
            for alias in node.names:
                if alias.name == "*" or hasattr(imported, alias.name):
                    continue
                try:
                    importlib.import_module(f"{target}.{alias.name}")
                except ImportError:
                    rel = path.relative_to(SRC.parent).as_posix()
                    found.append(f"{rel}:{node.lineno}: from {target} import {alias.name}")
    return sorted(found)


def test_no_first_party_import_names_a_symbol_its_module_lacks() -> None:
    dangling = _dangling_names()
    assert not dangling, (
        "These imports name a symbol their module does not define. Under "
        "`TYPE_CHECKING` nothing raises at runtime and the repository ships no type "
        "checker, so the annotation silently refers to nothing:\n  " + "\n  ".join(dangling)
    )
