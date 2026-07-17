"""Import-hygiene pin, in force since the first commit (HATS-1020).

ai_hats_rack is built PARALLEL to the production stack: it must not import
the integrator, tracker, wt, or any git plumbing — the kernel never shells
out. AST-level check so even deferred / TYPE_CHECKING imports are caught.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "ai_hats_rack"

FORBIDDEN = (
    "ai_hats",  # the integrator (prefix-checked: ai_hats.* too)
    "ai_hats_core",
    "ai_hats_tracker",
    "ai_hats_wt",
    "ai_hats_observe",
    "ai_hats_library",
    "subprocess",  # no git plumbing / shelling out in the kernel
    "git",
)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
    return found


def test_rack_source_exists():
    assert SRC.is_dir(), f"package source missing at {SRC}"
    assert (SRC / "__init__.py").is_file()


def test_rack_imports_no_first_party_or_git():
    offenders: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        for module in _imported_modules(py):
            if any(module == f or module.startswith(f + ".") for f in FORBIDDEN):
                offenders.append(f"{py.relative_to(SRC)}: imports {module}")
    assert not offenders, "forbidden imports in ai_hats_rack:\n" + "\n".join(offenders)
