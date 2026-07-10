"""Import-boundary guard for ai-hats-library (HATS-876 / T18).

A data package: it declares ZERO dependencies and its importable shim
(``ai_hats_library/__init__.py``) imports only the standard library — never
``ai_hats`` or a third party. This is what keeps it standalone-consumable and
symmetric with the engines. (The hook scripts shipped under ``*/skills/*/hooks``
are DATA materialised into projects, not part of the import surface, so they are
out of scope here.)
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PKG_ROOT / "src" / "ai_hats_library" / "__init__.py"


def test_declares_zero_dependencies() -> None:
    meta = tomllib.loads((_PKG_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"]["dependencies"] == []


def test_shim_imports_stdlib_only() -> None:
    tree = ast.parse(_INIT.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    roots.discard("__future__")
    non_stdlib = {r for r in roots if r not in sys.stdlib_module_names}
    assert not non_stdlib, f"shim must import stdlib only; found: {sorted(non_stdlib)}"
