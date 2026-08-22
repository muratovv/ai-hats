"""HATS-1783 — the pipeline area is entered through its ``__init__`` and nowhere else.

Three gates, all negative universals (ADR-0026 D3): they assert the absence of a
second path, so they cannot go green while one survives.

The two ratchets carry a baseline of what is left to convert: it may fall, never
grow. That is how the next contributor learns the format — a fresh
``from ..pipeline.keys import KEY_X`` fails here with the list of offenders, instead
of quietly becoming the twelfth way in.

Edge counting follows ADR-0026 D5 and the 2026-08-21 ruling on F4: deferred and
``TYPE_CHECKING`` imports count exactly like module-level ones. A boundary blind to
them is blind to 45% of this graph.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
AREA = "ai_hats.pipeline"

# Deep entries (an import naming anything under the area other than the area
# itself) still to convert. HATS-1783 lowers this to 0; it must never rise.
BASELINE_DEEP_ENTRIES = 20

# Modules outside the area that still dispatch a pipeline themselves instead of
# calling ``run_pipeline``. HATS-1783 lowers this to 0.
BASELINE_FOREIGN_DISPATCHERS = 2

_DISPATCH_ENTRY_POINTS = frozenset(
    {
        f"{AREA}.harness",
        f"{AREA}.loader",
        f"{AREA}.presets",
    }
)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _import_targets(tree: ast.AST, module: str, is_package: bool) -> list[tuple[str, str]]:
    """Every (target_module, imported_name) this module names, at any import depth."""
    base = module if is_package else module.rsplit(".", 1)[0]
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((alias.name, alias.name.rsplit(".", 1)[-1]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = base.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                target = ".".join(parts + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            out.extend((target, alias.name) for alias in node.names)
    return out


def _outside_modules() -> list[tuple[str, Path, ast.AST]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        if module == AREA or module.startswith(f"{AREA}."):
            continue
        found.append((module, path, ast.parse(path.read_text())))
    return found


def _deep_entries() -> list[str]:
    """``module -> ai_hats.pipeline.<something>`` — every import past the facade."""
    entries = []
    for module, path, tree in _outside_modules():
        for target, name in _import_targets(tree, module, path.name == "__init__.py"):
            if target.startswith(f"{AREA}.") or (target == AREA and f"{AREA}.{name}" != AREA):
                # `from ai_hats import pipeline` names the area itself, not a part of it.
                if target == AREA and name == "pipeline":
                    continue
                if target == AREA and not (SRC / "ai_hats" / "pipeline" / f"{name}.py").exists():
                    continue
                entries.append(f"{module} -> {target}.{name}" if target == AREA else f"{module} -> {target}")
    return sorted(entries)


def _foreign_dispatchers() -> list[str]:
    """Modules outside the area that build or run a pipeline on their own."""
    found = set()
    for module, path, tree in _outside_modules():
        for target, _name in _import_targets(tree, module, path.name == "__init__.py"):
            if target in _DISPATCH_ENTRY_POINTS:
                found.add(module)
    return sorted(found)


def test_no_new_deep_entry_into_the_pipeline_area() -> None:
    entries = _deep_entries()
    assert len(entries) <= BASELINE_DEEP_ENTRIES, (
        "a new import enters ai_hats.pipeline past its __init__ — the area's public "
        "contract is ai_hats/pipeline/__init__.py, and everything else in it is "
        "internal (ADR-0026 D14). Entries now:\n  " + "\n  ".join(entries)
    )
    assert len(entries) == BASELINE_DEEP_ENTRIES, (
        f"deep entries dropped to {len(entries)} — lower BASELINE_DEEP_ENTRIES to match, "
        "so the ratchet keeps the ground it won."
    )


def test_no_new_pipeline_dispatcher_outside_the_area() -> None:
    dispatchers = _foreign_dispatchers()
    assert len(dispatchers) <= BASELINE_FOREIGN_DISPATCHERS, (
        "a module outside the area dispatches a pipeline itself; the single entry "
        "point is pipeline.run_pipeline (ADR-0026 C9). Dispatchers now:\n  "
        + "\n  ".join(dispatchers)
    )
    assert len(dispatchers) == BASELINE_FOREIGN_DISPATCHERS, (
        f"foreign dispatchers dropped to {len(dispatchers)} — lower "
        "BASELINE_FOREIGN_DISPATCHERS to match."
    )


def test_every_shipped_pipeline_is_declared_in_the_catalog() -> None:
    """A pipeline YAML nobody declared is a pipeline nothing can launch."""
    from ai_hats.paths.library import builtin_library_root
    from ai_hats import pipeline_catalog

    root = builtin_library_root()
    if root is None:  # pragma: no cover — a broken install, not a contract breach
        pytest.skip("builtin library root unresolved")
    shipped = {path.stem for path in (root / "core" / "pipelines").glob("*.yaml")}
    declared = {config.name for config in pipeline_catalog.ALL}
    assert shipped == declared, (
        "the shipped pipelines and the application catalog disagree — declare the new "
        f"pipeline in ai_hats/pipeline_catalog.py.\n  only shipped: {sorted(shipped - declared)}"
        f"\n  only declared: {sorted(declared - shipped)}"
    )
