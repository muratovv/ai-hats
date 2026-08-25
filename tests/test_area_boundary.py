"""HATS-1783 — what the ``pipeline`` area's boundary asserts today, stated exactly.

Five gates. Three are the negative universals ADR-0026 D3 asks for — the cycle set
(empty since the step registry stopped resolving by import), the registration gate, and
the catalog. The other two pin the breach set this epic is still shrinking, so a green
run there means "the ways in did not change", never "there is no way in".

The three import gates read one tree — the modules the wheel ships (see
``_source_modules``) — through one walk, so "an import" means the same thing in all of
them rather than one thing per gate. The fourth reads the shipped YAML; the fifth
imports that same tree in a subprocess and reads what it did to the registry.

1. **Deep entries.** Asserts that the imports naming something *under* the area
   rather than the area itself equal ``PINNED_DEEP_ENTRIES`` as a multiset. Does not
   assert that the area is entered only through its ``__init__``: every pinned entry
   is a live breach of the facade. The entries are pinned rather than counted on
   purpose — a count stays equal when one entry is converted and another added, and
   that swap is exactly the regression this gate exists to catch.
2. **Python-assembled pipelines.** Asserts that the pipelines assembled in code,
   rather than loaded from their YAML, equal ``PINNED_PYTHON_ASSEMBLED``. The subject
   is ADR-0026 C9 — "one dispatcher, no second path" — so the gate follows the
   property in every spelling that reaches it, not a list of module names and not one
   constructor: the facade re-exports ``build`` *and* ``Pipeline``, and
   ``pipeline.py`` supports calling the class directly, so both are full second paths
   and a gate watching one of them reads green on the other. Exempt are two named
   calls, not two files (§5 row 6): ``loader.load_pipeline``, which is the YAML path
   itself, and the body of ``build``, which is the constructor. The structural fix is
   to stop exporting ``build``, ``Pipeline`` and ``run`` from the area's ``__init__``
   and leave the loader as the only way in; it waits on ``cli.assembly``, which still
   builds its ``preview`` pipeline in Python and is carded as HATS-1784. Until then
   this gate does not assert that YAML is the only way a pipeline is assembled; it
   asserts that a fourth way cannot arrive unnoticed.
3. **Modules in a cycle.** Asserts that the area's modules sitting in a non-trivial
   strongly connected component of the import graph equal
   ``PINNED_AREA_MODULES_IN_A_CYCLE``. ADR-0026 D12 makes this pilot gate 0 and the
   pin is now 0, which turns it from a ratchet into an absolute: any area module in
   any cycle is red, and the re-pin literal below it exists only to name the offender.
4. **Catalog.** Set equality between the shipped pipeline YAML and the catalog the
   application declares. This one is a true negative universal: it cannot go green
   while a shipped pipeline is undeclared. It fails, and never skips, when the
   library root does not resolve — a gate that can excuse itself is the failure this
   epic exists to remove.
5. **Registration by import.** Imports every module of gate 1-3's tree in a fresh
   process and asserts the step registry came out empty. The subject is the mechanism
   gate 3 depends on: built-in steps are *declared* (the ``ai_hats.steps`` entry-point
   group) and imported per id, never registered as a side effect of importing them.
   Behavioural on purpose — an AST gate would watch calls to ``register`` and miss
   ``_REGISTRY[name] = cls``, an ``importlib`` call, or any other spelling of the same
   side effect. ``register`` itself stays legal: user steps and out-of-tree packages
   call it, and neither ships in this tree.

Re-pinning is mechanical by design. A pin that stops matching prints the
added/removed diff and then the exact literal to paste back, sorted and one entry per
line. Regenerate a pin from its failing run; a hand-edited pin is how a breach gets
absorbed instead of noticed.

Entry counting covers deferred and ``TYPE_CHECKING`` imports (ADR-0026 D5 and the
2026-08-21 ruling on F4), and the share is worth stating with its definition rather
than as a slogan: of the 1 877 import statements in the 185 modules ``_source_modules``
walks, 607 — 32% — are written inside a function body or under ``if TYPE_CHECKING``.
That is what a boundary lint reading only module-level imports does not see. An earlier
draft of this line said 45% and named no definition; re-measure with the walk below.
"""  # comment-length: allow — a gate is only as honest as its statement of what it misses

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
AREA = "ai_hats.pipeline"

# comment-length: allow — a small pin that will grow has to say so, or it reads as done
# The second area (HATS-1826). Four of its five implementations still ship as separate
# distributions under packages/surfaces/, so today this name covers `claude` alone: the
# pin below GROWS as each surface moves in, and is driven back down from there. There is
# also no facade yet — `src/ai_hats/surfaces/__init__.py` does not exist — so every entry
# here is deep by definition, and 2 is a starting line, not a near-clean tree.
SURFACES = "ai_hats.surfaces"

# From the test's own location, not via ``builtin_library_root()``: that resolver honours
# AI_HATS_PROJECT_DIR, which in a worktree names the MAIN checkout — so this gate compared
# one tree's YAML against another tree's catalog, and a worktree adding a pipeline stayed green.
LIBRARY_ROOT = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"

# comment-length: allow — what is left has to say why it is left, or the pin reads as done
# Every import that names a part of the area instead of the area itself. Each entry is a
# live breach of the facade (ADR-0026 D14). HATS-1783 took the other 17: the finalize
# sub-pipelines run through ``run_subpipeline`` and the preload through ``warm``, so
# runtime_common and wrap_runner name no area module at all, and the three consumers that
# only spelled ``HarnessPolicy`` under TYPE_CHECKING read it off the facade. The three
# left are one defect, not three — ``cli.assembly`` builds the ``preview`` pipeline in
# Python, so it needs ``build`` and the two steps it assembles; it is the same second path
# PINNED_PYTHON_ASSEMBLED holds, and it closes with that one (HATS-1784).
PINNED_DEEP_ENTRIES: tuple[str, ...] = (
    "ai_hats.cli.assembly -> ai_hats.pipeline.pipeline",
    "ai_hats.cli.assembly -> ai_hats.pipeline.steps.emit",
    "ai_hats.cli.assembly -> ai_hats.pipeline.steps.materialize",
)

# comment-length: allow — the two entries and the zero next to them are the whole slice
# Every import naming a part of the surfaces area instead of the area itself. Both are
# the same shape: a caller that wants ONE implementation by name rather than the
# `Provider` contract every implementation answers. Measured at HATS-1826 S1; the entries
# a fold ADDS are the slice's work list.
#
# Zero is the number to keep in view here: `src/ai_hats/**` imports `ai_hats_agy`,
# `ai_hats_cline`, `ai_hats_codex` and `ai_hats_opencode` exactly **0** times — the
# shipped integrator never reaches into a surface package, it resolves them through the
# `ai_hats.providers` entry-point group. So folding them in rewrites no shipped import;
# the import churn lives in tests/, and this pin is what keeps it from moving to src/.
PINNED_SURFACES_DEEP_ENTRIES: tuple[str, ...] = (
    "ai_hats.providers -> ai_hats.surfaces.claude.provider",
    "ai_hats.sweeper -> ai_hats.surfaces.claude.provider",
)

# Every pipeline assembled in code instead of loaded from its YAML. Each is a second
# path past the loader (ADR-0026 C9). Two of the three went with ``pipeline.presets``,
# which had no production reader (HATS-1783); the last one is ``preview`` (HATS-1784).
PINNED_PYTHON_ASSEMBLED: tuple[str, ...] = ("ai_hats.cli.assembly -> build(name='preview')",)

# comment-length: allow — an empty pin has to say what emptied it, or nobody can defend it
# Every module of the area sitting in a non-trivial SCC. ADR-0026 D12 sets this gate at 0,
# and HATS-1783 cut it there: the cycle ran loader -> steps -> steps.handoff -> cli.reflect
# -> the facade, held together by one import whose only job was the side effect of
# registering the built-in steps. Built-ins are now declared under the `ai_hats.steps`
# entry-point group and imported per id, so the loader names no step at all. Empty, this
# pin is the negative universal D3 asks for — one area module back in any cycle is red.
# ai_hats.pipeline_catalog sits in the surviving 26-module SCC and is absent here on
# purpose: it is application code, and "ai_hats.pipeline" is its prefix only as a string.
PINNED_AREA_MODULES_IN_A_CYCLE: tuple[str, ...] = ()

# Both modules bind the same constructors: ``__init__`` re-exports them from
# ``pipeline.pipeline``, so a gate that watches only one of them watches neither.
_ASSEMBLY_SOURCES = frozenset({AREA, f"{AREA}.pipeline"})

# comment-length: allow — the second constructor is the incident this line exists for
# Every name that returns an assembled pipeline. ``Pipeline`` is here because it is a
# second public constructor: ``from ai_hats.pipeline import Pipeline as _P; _P(steps=(),
# name="x")`` assembles a pipeline in code, passed every gate, and was not even a deep
# entry — the name sits on the facade. See gate 2 above for the structural fix.
_ASSEMBLY_CONSTRUCTORS = frozenset({"build", "Pipeline"})

# comment-length: allow — an exemption has to say what it still lets past, or it is a hole
# The sanctioned assemblies, named as ``(module, function)`` — the call, not the file
# (§5 row 6 of docs/adr/attachments/area-extraction-notes.md). ``loader.load_pipeline``
# *is* the YAML path rather than a mirror of it, and ``pipeline.build`` is the
# constructor's own body.
# Exempting their **modules** is what this gate did before, and it made a second, non-YAML
# assembly written anywhere inside ``loader.py`` invisible. What the narrowing still lets
# past, so it can be argued with: a second assembly written into the body of one of these
# two functions.
_SANCTIONED_ASSEMBLY_SITES: tuple[tuple[str, str], ...] = (
    (f"{AREA}.loader", "load_pipeline"),
    (f"{AREA}.pipeline", "build"),
)


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


def _import_targets(tree: ast.AST, module: str, is_package: bool) -> list[tuple[str, str | None]]:
    """Every (target_module, imported_name) this module names, at any import depth.

    ``import a.b`` names a module and no member, so its name is ``None``: read as a
    member it turns every plain import of a package into an import of ``pkg.pkg``.
    """
    base = _base_package(module, is_package)
    out: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((alias.name, None) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = _from_target(node, base)
            out.extend((target, alias.name) for alias in node.names)
    return out


def _source_modules() -> list[tuple[str, Path, ast.AST]]:
    """Every module the wheel ships — the tree the three import gates share.

    The scope is pyproject's own ``exclude = ["src/ai_hats/**/tests"]`` (ADR-0026
    D5/D11), not a qualifier invented here: the area's tests exist to call the
    constructors and reach the internals these gates forbid everyone else. What it
    misses, so it can be argued with: an assembly path parked inside a ``tests``
    package under ``src/`` is invisible here — and unshippable.
    """
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.relative_to(SRC).parts:
            continue
        found.append((_module_name(path), path, ast.parse(path.read_text())))
    return found


def _outside_modules(area: str = AREA) -> list[tuple[str, Path, ast.AST]]:
    return [
        entry
        for entry in _source_modules()
        if not (entry[0] == area or entry[0].startswith(f"{area}."))
    ]


def _area_submodules(area: str = AREA) -> frozenset[str]:
    """Everything under the area that a name can reach — modules and subpackages alike.

    Taken from the module inventory, not from ``<name>.py`` on disk: that spelling saw
    files and missed folders, so ``from ai_hats.pipeline import steps`` — a subpackage,
    entered past the facade — was invisible, as was the module whose name repeats its
    package (``from ai_hats.pipeline import pipeline``, which is where ``build`` lives).
    """
    return frozenset(
        module
        for module, _path, _tree in _source_modules()
        if module != area and module.startswith(f"{area}.")
    )


def _deep_entries(area: str = AREA) -> tuple[str, ...]:
    """``module -> <area>.<something>`` — every import past the facade."""
    submodules = _area_submodules(area)
    entries = []
    for module, path, tree in _outside_modules(area):
        for target, name in _import_targets(tree, module, path.name == "__init__.py"):
            if target in submodules:
                entries.append(f"{module} -> {target}")
            elif target == area and name is not None and f"{area}.{name}" in submodules:
                # A name the facade re-exports is the contract; a name that *is* a
                # module under the area is the same breach spelled through __init__.
                entries.append(f"{module} -> {area}.{name}")
    return tuple(sorted(entries))


def _assembler_spellings(tree: ast.AST, module: str, is_package: bool) -> dict[str, str]:
    """How this module spells each of the area's pipeline constructors: spelling -> name.

    The imported name (``build``, ``build as build_pipeline``, ``Pipeline as _P``), the
    qualified form (``pipeline.build`` after ``from ai_hats import pipeline``), and — in
    the module that *defines* them — the bare name, because the second path this gate
    watches for is free to arrive as any of them. Each maps back to the constructor it
    binds, so the pin below names the constructor and not somebody's local alias.
    """
    base = _base_package(module, is_package)
    spellings = {
        f"{source}.{constructor}": constructor
        for source in _ASSEMBLY_SOURCES
        for constructor in _ASSEMBLY_CONSTRUCTORS
    }
    if module in _ASSEMBLY_SOURCES:
        spellings.update({constructor: constructor for constructor in _ASSEMBLY_CONSTRUCTORS})
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = _from_target(node, base)
            for alias in node.names:
                if target in _ASSEMBLY_SOURCES and alias.name in _ASSEMBLY_CONSTRUCTORS:
                    spellings[alias.asname or alias.name] = alias.name
                elif f"{target}.{alias.name}" in _ASSEMBLY_SOURCES:
                    qualifier = alias.asname or alias.name
                    spellings.update({f"{qualifier}.{c}": c for c in _ASSEMBLY_CONSTRUCTORS})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _ASSEMBLY_SOURCES and alias.asname:
                    spellings.update({f"{alias.asname}.{c}": c for c in _ASSEMBLY_CONSTRUCTORS})
    return spellings


def _sanctioned_calls(module: str, tree: ast.AST) -> set[ast.Call]:
    """The calls this module makes from inside a sanctioned assembly site.

    Identity, not position: the exemption is the body of one named function, so a call
    written anywhere else in the same file is not covered by it.
    """
    exempt: set[ast.Call] = set()
    for site_module, site_function in _SANCTIONED_ASSEMBLY_SITES:
        if module != site_module:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == site_function:
                exempt |= {call for call in ast.walk(node) if isinstance(call, ast.Call)}
    return exempt


def _assembled_name(node: ast.Call) -> str:
    """The pipeline the call names, as written — the YAML this assembly mirrors.

    ``<unnamed>`` when the call passes no ``name=``: the entry is still red, it just
    cannot say which pipeline it built.
    """
    for keyword in node.keywords:
        if keyword.arg == "name":
            return ast.unparse(keyword.value)
    return "<unnamed>"


def _python_assembled_pipelines() -> tuple[str, ...]:
    """Every pipeline assembled from something other than its YAML."""
    found = []
    for module, path, tree in _source_modules():
        sanctioned = _sanctioned_calls(module, tree)
        spellings = _assembler_spellings(tree, module, path.name == "__init__.py")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or node in sanctioned:
                continue
            constructor = spellings.get(ast.unparse(node.func))
            if constructor is not None:
                found.append(f"{module} -> {constructor}(name={_assembled_name(node)})")
    return tuple(sorted(found))


def _import_edges() -> dict[str, set[str]]:
    """The shipped import graph: ``module -> every shipped module it names``.

    ``from pkg import name`` reaches both ``pkg`` and ``pkg.name``, because importing
    a submodule really does execute its package. The one edge dropped is a module to
    its **own ancestor package** when the same statement also names a real submodule:
    ``from . import sdk_runner`` depends on the sibling, and touches the ``__init__``
    only because that is how Python spells "the sibling". Keeping it makes every
    package that re-exports a submodule a 2-cycle with it — an artefact of edge
    resolution, not a cycle anyone can cut. A module importing a name that only its
    package's ``__init__`` defines keeps that edge: nothing else defines it.
    """  # comment-length: allow — this rule decides which cycles the pin below counts
    modules = {module: (path, tree) for module, path, tree in _source_modules()}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, (path, tree) in modules.items():
        for target, name in _import_targets(tree, module, path.name == "__init__.py"):
            submodule = f"{target}.{name}" if name is not None else target
            reaches_submodule = submodule != target and submodule in modules
            for candidate in (target, submodule):
                if candidate not in modules or candidate == module:
                    continue
                if candidate == target and reaches_submodule and module.startswith(f"{target}."):
                    continue
                graph[module].add(candidate)
    return graph


def _non_trivial_sccs(graph: dict[str, set[str]]) -> list[frozenset[str]]:
    """Tarjan, iterative — every strongly connected component of size > 1."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[frozenset[str]] = []
    counter = 0
    for root in graph:
        if root in index:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            if pending:
                child = pending.pop()
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, sorted(graph[child])))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    found.append(frozenset(component))
    return found


def _area_modules_in_a_cycle() -> tuple[str, ...]:
    """Every module of the area that sits in a non-trivial SCC of the shipped graph."""
    in_area = {
        module
        for component in _non_trivial_sccs(_import_edges())
        for module in component
        if module == AREA or module.startswith(f"{AREA}.")
    }
    return tuple(sorted(in_area))


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


def test_no_new_deep_entry_into_the_surfaces_area() -> None:
    entries = _deep_entries(SURFACES)
    assert entries == PINNED_SURFACES_DEEP_ENTRIES, _repin(
        "PINNED_SURFACES_DEEP_ENTRIES",
        "imports entering ai_hats.surfaces past its facade (ADR-0026 D14, HATS-1826)",
        PINNED_SURFACES_DEEP_ENTRIES,
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


def test_no_area_module_sits_in_an_import_cycle() -> None:
    in_a_cycle = _area_modules_in_a_cycle()
    assert in_a_cycle == PINNED_AREA_MODULES_IN_A_CYCLE, _repin(
        "PINNED_AREA_MODULES_IN_A_CYCLE",
        "modules of ai_hats.pipeline inside a non-trivial import SCC (ADR-0026 D12, target 0)",
        PINNED_AREA_MODULES_IN_A_CYCLE,
        in_a_cycle,
    )


# Imported, then asked what it registered — run in a child so the answer is about
# these imports and not about whatever the pytest process already loaded.
_REGISTRY_PROBE = """
import importlib, json, sys
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
from ai_hats.pipeline import registry
print(json.dumps(sorted(registry._REGISTRY)))
"""


def test_no_shipped_module_registers_a_step_by_being_imported() -> None:
    """Importing the whole shipped tree must leave the step registry empty.

    The mechanism gate 3 rests on: a built-in step is declared under the
    ``ai_hats.steps`` entry-point group and imported when its id is resolved, so no
    import populates the registry. When one does, the loader is back to depending on
    every step module, and the cycle comes with it (HATS-1783).
    """
    modules = [module for module, _path, _tree in _source_modules() if module.startswith("ai_hats")]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path)
    probe = subprocess.run(
        [sys.executable, "-c", _REGISTRY_PROBE, json.dumps(modules)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 0, (
        "the probe could not import the shipped tree, so this gate checked nothing:\n"
        f"{probe.stdout}\n{probe.stderr}"
    )
    registered = json.loads(probe.stdout)
    assert registered == [], (
        f"importing the shipped tree registered {registered} — a built-in step is being "
        "registered as an import side effect again. Declare it under the "
        "`ai_hats.steps` entry-point group in pyproject.toml instead; `register()` is "
        "for user steps and out-of-tree packages, neither of which ships here."
    )


def test_every_shipped_pipeline_is_declared_in_the_catalog() -> None:
    """A pipeline YAML nobody declared is a pipeline nothing can launch."""
    from ai_hats import pipeline_catalog

    shipped_dir = LIBRARY_ROOT / "core" / "pipelines"
    assert shipped_dir.is_dir(), (
        f"the shipped pipelines are unreadable — {shipped_dir} is not a directory, so "
        "this gate has checked nothing. It fails rather than skips: a gate that "
        "excuses itself is the hole this epic closes."
    )
    shipped = {path.stem for path in shipped_dir.glob("*.yaml")}
    declared = {config.name for config in pipeline_catalog.ALL}
    assert shipped == declared, (
        "the shipped pipelines and the application catalog disagree — declare the new "
        f"pipeline in ai_hats/pipeline_catalog.py.\n  only shipped: {sorted(shipped - declared)}"
        f"\n  only declared: {sorted(declared - shipped)}"
    )
