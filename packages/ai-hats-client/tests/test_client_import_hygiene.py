"""The client imports nothing first-party: it relies on the holder's pipes, never on ai-hats."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "ai_hats_client"

FORBIDDEN = (
    "ai_hats",
    "ai_hats_core",
    "ai_hats_observe",
    "ai_hats_library",
    "ai_hats_rack",
    "ai_hats_wt",
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


def test_the_source_is_there() -> None:
    assert (SRC / "__init__.py").is_file(), f"package source missing at {SRC}"


def test_the_client_imports_no_first_party_module() -> None:
    offenders = [
        f"{py.relative_to(SRC)}: imports {module}"
        for py in sorted(SRC.rglob("*.py"))
        for module in _imported_modules(py)
        if any(module == f or module.startswith(f + ".") for f in FORBIDDEN)
    ]

    assert not offenders, "first-party imports in ai_hats_client:\n" + "\n".join(offenders)
