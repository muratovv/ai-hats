"""HATS-1783 — what the ``pipeline`` area's boundary asserts today, stated exactly.

Three gates, and only the third is the negative universal ADR-0026 D3 asks for. The
other two pin the breach set this epic is shrinking, so a green run here means "the
ways in did not change", never "there is no way in".

1. **Deep entries.** Asserts that the imports naming something *under* the area
   rather than the area itself equal ``PINNED_DEEP_ENTRIES`` as a multiset. Does not
   assert that the area is entered only through its ``__init__``: every pinned entry
   is a live breach of the facade. The entries are pinned rather than counted on
   purpose — a count stays equal when one entry is converted and another added, and
   that swap is exactly the regression this gate exists to catch.
2. **Python-assembled pipelines.** Asserts that the pipelines built by calling the
   area's ``build`` constructor, rather than loaded from their YAML, equal
   ``PINNED_PYTHON_ASSEMBLED``. The subject is ADR-0026 C9 — "one dispatcher, no
   second path" — so the gate follows the constructor, not a list of module names:
   the facade re-exports ``build``, which makes ``from ai_hats.pipeline import build``
   a full second path that no list of internal modules can see. Does not assert that
   YAML is the only way a pipeline is assembled; it asserts that a fourth way cannot
   arrive unnoticed.
3. **Catalog.** Set equality between the shipped pipeline YAML and the catalog the
   application declares. This one is a true negative universal: it cannot go green
   while a shipped pipeline is undeclared. It fails, and never skips, when the
   library root does not resolve — a gate that can excuse itself is the failure this
   epic exists to remove.

Re-pinning is mechanical by design. A pin that stops matching prints the
added/removed diff and then the exact literal to paste back, sorted and one entry per
line. Regenerate a pin from its failing run; a hand-edited pin is how a breach gets
absorbed instead of noticed.

Entry counting covers deferred and ``TYPE_CHECKING`` imports (ADR-0026 D5 and the
2026-08-21 ruling on F4) — a boundary blind to them is blind to 45% of this graph.
"""  # comment-length: allow — a gate is only as honest as its statement of what it misses

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
AREA = "ai_hats.pipeline"

# Every import that names a part of the area instead of the area itself. Each entry
# is a live breach of the facade (ADR-0026 D14); HATS-1783 drives this to ().
PINNED_DEEP_ENTRIES: tuple[str, ...] = (
    "ai_hats.cli -> ai_hats.pipeline.harness",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.harness",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.keys",
    "ai_hats.cli.assembly -> ai_hats.pipeline.pipeline",
    "ai_hats.cli.assembly -> ai_hats.pipeline.steps.emit",
    "ai_hats.cli.assembly -> ai_hats.pipeline.steps.materialize",
    "ai_hats.cli.reflect -> ai_hats.pipeline.harness",
    "ai_hats.cli.reflect -> ai_hats.pipeline.harness",
    "ai_hats.cli.reflect -> ai_hats.pipeline.harness",
    "ai_hats.cli.reflect -> ai_hats.pipeline.harness",
    "ai_hats.cli.reflect -> ai_hats.pipeline.harness",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect_session_main -> ai_hats.pipeline.harness",
    "ai_hats.cli.reflect_session_main -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect_session_main -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect_session_main -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect_session_main -> ai_hats.pipeline.keys",
    "ai_hats.cli.reflect_session_main -> ai_hats.pipeline.keys",
    "ai_hats.harness.guard -> ai_hats.pipeline.harness_policy",
    "ai_hats.retro.session_review_runner -> ai_hats.pipeline.harness_policy",
    "ai_hats.runtime_common -> ai_hats.pipeline.keys",
    "ai_hats.runtime_common -> ai_hats.pipeline.keys",
    "ai_hats.runtime_common -> ai_hats.pipeline.keys",
    "ai_hats.runtime_common -> ai_hats.pipeline.keys",
    "ai_hats.runtime_common -> ai_hats.pipeline.keys",
    "ai_hats.runtime_common -> ai_hats.pipeline.keys",
    "ai_hats.runtime_common -> ai_hats.pipeline.keys",
    "ai_hats.runtime_common -> ai_hats.pipeline.keys",
    "ai_hats.runtime_common -> ai_hats.pipeline.loader",
    "ai_hats.runtime_common -> ai_hats.pipeline.loader",
    "ai_hats.runtime_common -> ai_hats.pipeline.pipeline",
    "ai_hats.runtime_common -> ai_hats.pipeline.pipeline",
    "ai_hats.subagent_runner -> ai_hats.pipeline.harness_policy",
    "ai_hats.wrap_runner -> ai_hats.pipeline.keys",
    "ai_hats.wrap_runner -> ai_hats.pipeline.loader",
)

# Every pipeline assembled by calling ``build`` instead of loading its YAML. Each is
# a second path past the loader (ADR-0026 C9); HATS-1783 drives this to ().
PINNED_PYTHON_ASSEMBLED: tuple[str, ...] = (
    "ai_hats.cli.assembly -> build(name='preview')",
    "ai_hats.pipeline.presets -> build(name=PIPELINE_EXECUTE)",
    "ai_hats.pipeline.presets -> build(name=PIPELINE_INIT)",
)

# Both spellings bind the same constructor: ``__init__`` re-exports ``build`` from
# ``pipeline.pipeline``, so a gate that watches only one of them watches neither.
_BUILD_SOURCES = frozenset({AREA, f"{AREA}.pipeline"})

# The one sanctioned caller: ``loader.load_pipeline`` *is* the YAML path, not a
# mirror of it, so its own ``build`` call is the assembly every other one bypasses.
_YAML_ASSEMBLER = f"{AREA}.loader"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _base_package(module: str, is_package: bool) -> str:
    return module if is_package else module.rsplit(".", 1)[0]


def _from_target(node: ast.ImportFrom, base: str) -> str:
    """The absolute module an ``ImportFrom`` names, with relative levels resolved."""
    if not node.level:
        return node.module or ""
    parts = base.split(".")
    parts = parts[: len(parts) - (node.level - 1)]
    return ".".join(parts + ([node.module] if node.module else []))


def _import_targets(tree: ast.AST, module: str, is_package: bool) -> list[tuple[str, str]]:
    """Every (target_module, imported_name) this module names, at any import depth."""
    base = _base_package(module, is_package)
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((alias.name, alias.name.rsplit(".", 1)[-1]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = _from_target(node, base)
            out.extend((target, alias.name) for alias in node.names)
    return out


def _source_modules() -> list[tuple[str, Path, ast.AST]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found.append((_module_name(path), path, ast.parse(path.read_text())))
    return found


def _outside_modules() -> list[tuple[str, Path, ast.AST]]:
    return [
        entry
        for entry in _source_modules()
        if not (entry[0] == AREA or entry[0].startswith(f"{AREA}."))
    ]


def _deep_entries() -> tuple[str, ...]:
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
                entries.append(
                    f"{module} -> {target}.{name}" if target == AREA else f"{module} -> {target}"
                )
    return tuple(sorted(entries))


def _assembler_spellings(tree: ast.AST, module: str, is_package: bool) -> set[str]:
    """How this module would spell a call to the area's ``build`` constructor.

    Both the imported name (``build``, ``build as build_pipeline``) and the qualified
    form (``pipeline.build`` after ``from ai_hats import pipeline``), because the
    second path this gate watches for is free to arrive as either.
    """
    base = _base_package(module, is_package)
    spellings = {f"{source}.build" for source in _BUILD_SOURCES}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = _from_target(node, base)
            for alias in node.names:
                if target in _BUILD_SOURCES and alias.name == "build":
                    spellings.add(alias.asname or alias.name)
                elif f"{target}.{alias.name}" in _BUILD_SOURCES:
                    spellings.add(f"{alias.asname or alias.name}.build")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _BUILD_SOURCES and alias.asname:
                    spellings.add(f"{alias.asname}.build")
    return spellings


def _assembled_name(node: ast.Call) -> str:
    """The pipeline the call names, as written — the YAML this assembly mirrors."""
    for keyword in node.keywords:
        if keyword.arg == "name":
            return ast.unparse(keyword.value)
    return "<unnamed>"


def _python_assembled_pipelines() -> tuple[str, ...]:
    """Every pipeline assembled from something other than its YAML."""
    found = []
    for module, path, tree in _source_modules():
        if module == _YAML_ASSEMBLER:
            continue
        spellings = _assembler_spellings(tree, module, path.name == "__init__.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in spellings:
                found.append(f"{module} -> build(name={_assembled_name(node)})")
    return tuple(sorted(found))


def _repin(constant: str, subject: str, pinned: tuple[str, ...], actual: tuple[str, ...]) -> str:
    """The diff against the pin, then the literal to paste in its place."""
    have, want = Counter(actual), Counter(pinned)
    lines = [f"{subject}: {len(actual)} found, {len(pinned)} pinned in {constant}."]
    for label, delta in (("added", have - want), ("removed", want - have)):
        lines.extend(f"  {label}: {entry}" for entry in sorted(delta.elements()))
    lines.append(f"\nWhen every line above is intended, replace {constant} with exactly:\n")
    lines.append(f"{constant}: tuple[str, ...] = (")
    lines.extend(f"    {json.dumps(entry)}," for entry in actual)
    lines.append(")")
    return "\n".join(lines)


def test_no_new_deep_entry_into_the_pipeline_area() -> None:
    entries = _deep_entries()
    assert entries == PINNED_DEEP_ENTRIES, _repin(
        "PINNED_DEEP_ENTRIES",
        "imports entering ai_hats.pipeline past its __init__ (ADR-0026 D14)",
        PINNED_DEEP_ENTRIES,
        entries,
    )


def test_no_pipeline_is_assembled_outside_its_yaml() -> None:
    assembled = _python_assembled_pipelines()
    assert assembled == PINNED_PYTHON_ASSEMBLED, _repin(
        "PINNED_PYTHON_ASSEMBLED",
        "pipelines assembled in Python rather than loaded from their YAML (ADR-0026 C9)",
        PINNED_PYTHON_ASSEMBLED,
        assembled,
    )


def test_every_shipped_pipeline_is_declared_in_the_catalog() -> None:
    """A pipeline YAML nobody declared is a pipeline nothing can launch."""
    from ai_hats import pipeline_catalog
    from ai_hats.paths.library import builtin_library_root

    root = builtin_library_root()
    assert root is not None, (
        "builtin_library_root() resolved nothing, so the shipped pipeline YAML could "
        "not be listed and this gate has checked nothing. It fails rather than skips: "
        "a gate that excuses itself is the hole this epic closes."
    )
    shipped_dir = root / "core" / "pipelines"
    assert shipped_dir.is_dir(), (
        f"the shipped pipelines are unreadable — {shipped_dir} is not a directory, so "
        "this gate has checked nothing."
    )
    shipped = {path.stem for path in shipped_dir.glob("*.yaml")}
    declared = {config.name for config in pipeline_catalog.ALL}
    assert shipped == declared, (
        "the shipped pipelines and the application catalog disagree — declare the new "
        f"pipeline in ai_hats/pipeline_catalog.py.\n  only shipped: {sorted(shipped - declared)}"
        f"\n  only declared: {sorted(declared - shipped)}"
    )
