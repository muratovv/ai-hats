"""The boundary guard for R1 (HATS-1193).

hats-relay is developed inside the ai-hats repository but must not depend on it: the
whole contract is the spawned binary plus the byte protocol. A human cannot be relied
on to notice an `import ai_hats` sneaking in, so the rule is mechanical.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
FORBIDDEN_ROOT = "ai_hats"


def imported_roots(tree: ast.AST) -> set[str]:
    """Top-level package of every import in ``tree`` (relative imports excluded)."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_detector_actually_detects():
    """Guard the guard: a vacuous detector would let every violation through."""
    violating = ast.parse("from ai_hats.pty_relay import wire_raw\nimport os\n")
    assert FORBIDDEN_ROOT in imported_roots(violating)
    assert FORBIDDEN_ROOT not in imported_roots(ast.parse("import os\nfrom . import wire\n"))


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: p.name)
def test_no_module_imports_ai_hats(path: Path):
    """hats-relay talks to ai-hats over a process boundary, never a Python import."""
    roots = imported_roots(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    assert FORBIDDEN_ROOT not in roots, (
        f"{path.relative_to(SRC)} imports {FORBIDDEN_ROOT}; the contract is the "
        f"spawned binary plus the byte protocol, not a Python import"
    )


def test_the_source_tree_was_actually_found():
    """A mistyped path would make the sweep above pass by scanning nothing."""
    assert SRC.is_dir()
    assert list(SRC.rglob("*.py"))
