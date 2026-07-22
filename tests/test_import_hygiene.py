"""HATS-758 — local import-hygiene gate.

A fast, dependency-free guard that runs in the normal pytest suite (local + CI),
so the import-structure class of problem surfaces **immediately** instead of only
on the slow, origin-only CodeQL run.

Policy encoded here — it matches how this codebase manages cycles, so it does not
re-create the CodeQL `security-and-quality` noise we dropped (HATS-758):

  * module-level **runtime** import cycles are FORBIDDEN (they break on import
    order and are genuine fragility);
  * `if TYPE_CHECKING:` blocks and deferred (function-body) imports are the
    project's deliberate cycle-management — they are intentionally IGNORED;
  * designated leaf modules import nothing first-party (so a shared constant can
    never again live in a high-level module and be imported back — the HATS-715
    regression this task fixes).

`import-linter` was evaluated and rejected (see plan): grimp counts deferred +
TYPE_CHECKING edges and cannot express "module-level runtime only", so it flags
the project's own idiom. This stdlib check expresses exactly the wanted policy.
"""

from __future__ import annotations

import ast
import graphlib
from pathlib import Path

PKG = "ai_hats"
SRC = Path(__file__).resolve().parent.parent / "src" / PKG
# Genuinely dependency-free foundations: they must import nothing first-party at
# ANY level (incl. deferred / TYPE_CHECKING). NB: `config` and the `models`
# facade are intentionally NOT here — config has real deferred deps (paths),
# the facade imports the domain modules. Keeping the list honest is what the
# gate enforces.
LEAF_MODULES = (
    "constants",
    "paths",  # HATS-862: git_env + safe_delete -> core
)

# HATS-863: schema modules must never regrow the models->providers back-edge —
# not even deferred (the pre-split cycle lived in a deferred validator import).
# tracker.models left for ai-hats-tracker (HATS-933); its purity is now the
# package's own test_boundary.py.
SCHEMA_MODULES = ("models", "config", "libraries.models")

# HATS-865: the composition layer is integrator-only (ADR-0014 Composition rule).
FORBIDDEN_COMPOSITION = ("composer", "assembler", "materialize", "providers", "resolver")

# Deny-by-default: only these may reference the layer — new modules are
# guarded automatically; extending the list is a deliberate, reviewed act.
ALLOWED_COMPOSITION_CONSUMERS = (
    *FORBIDDEN_COMPOSITION,  # the layer itself
    "cli",  # integrator orchestration (whole subtree)
    "migrations",
    "migration_assert",
    "migration_healer",
    "migration_v07",
    "relocation",
    "role_catalog",
    "costs",  # HATS-865: composition-tree introspection tooling, not a brick
    "composition_seam",  # HATS-865: THE integrator compose seam (payload builder)
    "sweeper",  # HATS-910 maintenance tooling (provider-managed surface sweep)
    # HATS-1023: consumer lifecycle union — enumerates ALL library skills by
    # design; a per-role CompositionPayload cannot express the union scope.
    "lifecycle_hooks",
    # HATS-1130: ec85f43d moved ClaudeProvider out of `providers` into
    # `surfaces/`. It subclasses Provider and reuses that module's markers, so
    # it IS the provider layer at a new path — not a brick reaching into it.
    "surfaces",
)

# HATS-865 T5 complete: the migration ratchet (EXPECTED_COMPOSITION_OFFENDERS)
# hit zero and was deleted — the gate below asserts NO offenders, ever.

# HATS-864 `test_layout_is_injected` + HATS-867 `test_observe_is_integrator_only`
# retired in T15 (HATS-948): observe left the integrator for `ai_hats_observe`
# (no `ai_hats.observe` module, no `paths.session_artifacts` shim), where
# `test_observe_boundary` / `test_workspace_boundaries` enforce the core-only package.


def _module_name(path: Path) -> str:
    parts = [PKG, *path.relative_to(SRC).with_suffix("").parts]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _import_nodes(tree: ast.Module, *, top_level_only: bool):
    """Yield Import/ImportFrom nodes, skipping ``if TYPE_CHECKING:`` blocks.

    ``top_level_only`` restricts to module-body statements (excludes deferred,
    function-level imports) — the runtime-cycle policy. Otherwise walk the whole
    tree (used for the stricter leaf-purity check).
    """
    if top_level_only:
        for node in tree.body:
            if isinstance(node, ast.If) and _is_type_checking(node.test):
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node
            elif isinstance(node, ast.If):  # non-TYPE_CHECKING top-level if (rare)
                for sub in (*node.body, *node.orelse):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        yield sub
        return
    # full walk (leaf-purity): include everything except TYPE_CHECKING blocks
    skip = {
        id(n)
        for branch in ast.walk(tree)
        if isinstance(branch, ast.If) and _is_type_checking(branch.test)
        for n in ast.walk(branch)
    }
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def _targets(module_name: str, is_pkg: bool, node, nodeset: set[str]) -> list[str]:
    """Resolve an import node to the first-party modules it references."""
    out: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == PKG or alias.name.startswith(PKG + "."):
                out.append(alias.name)
    elif isinstance(node, ast.ImportFrom):
        if node.level == 0:
            if node.module and (node.module == PKG or node.module.startswith(PKG + ".")):
                out.append(node.module)
                out += [f"{node.module}.{a.name}" for a in node.names]
        else:
            parts = module_name.split(".")
            pkg = parts if is_pkg else parts[:-1]
            if node.level > 1:  # ascend additional levels for `from .. import`
                pkg = pkg[: len(pkg) - (node.level - 1)]
            prefix = ".".join(pkg)
            if node.module:
                base = f"{prefix}.{node.module}" if prefix else node.module
                out.append(base)
                out += [f"{base}.{a.name}" for a in node.names]
            else:
                out += [f"{prefix}.{a.name}" if prefix else a.name for a in node.names]
    # Keep only references that are real modules in the graph.
    return [t for t in out if t in nodeset]


def _modules() -> dict[str, Path]:
    return {_module_name(p): p for p in SRC.rglob("*.py")}


def _runtime_graph() -> dict[str, set[str]]:
    """module -> set of first-party modules it imports at module-level runtime."""
    mods = _modules()
    nodeset = set(mods)
    graph: dict[str, set[str]] = {name: set() for name in nodeset}
    for name, path in mods.items():
        tree = ast.parse(path.read_text())
        is_pkg = path.name == "__init__.py"
        for node in _import_nodes(tree, top_level_only=True):
            graph[name].update(_targets(name, is_pkg, node, nodeset))
    return graph


def _find_cycle(graph: dict[str, set[str]]):
    """Return a list of nodes forming a cycle, or None. Edge direction is
    irrelevant for cycle *detection*, so we feed the graph straight in."""
    try:
        graphlib.TopologicalSorter(graph).prepare()
    except graphlib.CycleError as exc:
        return exc.args[1]
    return None


# --------------------------------------------------------------------------- #


def test_no_module_level_runtime_import_cycles():
    """No module under src/ai_hats may form a module-level runtime import cycle.

    TYPE_CHECKING and deferred (function-level) imports are ignored — they are
    the project's legitimate cycle-management.
    """
    cycle = _find_cycle(_runtime_graph())
    assert cycle is None, (
        "module-level runtime import cycle detected:\n  "
        + " -> ".join(cycle)
        + "\nBreak it by moving the shared symbol to a leaf module, or by "
        "deferring the import (function-level / TYPE_CHECKING)."
    )


def test_leaf_modules_are_pure():
    """Leaf modules must import nothing first-party — at any level (incl.
    TYPE_CHECKING). Keeps shared constants out of high-level modules (HATS-715).

    A leaf may be a single module OR a package (e.g. ``paths`` after the HATS-831
    split): every submodule is checked, and imports WITHIN the leaf's own package
    are allowed (internal cohesion) — only a dependency on a module OUTSIDE the
    leaf violates the invariant."""
    mods = _modules()
    nodeset = set(mods)
    offenders: dict[str, list[str]] = {}
    for leaf in LEAF_MODULES:
        name = f"{PKG}.{leaf}"
        prefix = name + "."  # the leaf's own package subtree (empty for plain modules)
        leaf_mods = {m: p for m, p in mods.items() if m == name or m.startswith(prefix)}
        refs: list[str] = []
        for m, path in leaf_mods.items():
            tree = ast.parse(path.read_text())
            for node in _import_nodes(tree, top_level_only=False):
                refs += [
                    t
                    for t in _targets(m, path.name == "__init__.py", node, nodeset)
                    if t != name and not t.startswith(prefix)  # ignore intra-leaf imports
                ]
        if refs:
            offenders[leaf] = sorted(set(refs))
    assert not offenders, (
        f"leaf modules must not import first-party (outside their own package): {offenders}"
    )


def test_schema_modules_never_import_providers():
    """Schema modules carry no ``providers`` reference at ANY level (HATS-863).

    The god-``models`` cycle survived via a deferred import inside a pydantic
    validator, so unlike the runtime-cycle gate this check walks the FULL tree
    (deferred imports included). RED under revert of the severing commit.
    """
    mods = _modules()
    nodeset = set(mods)
    providers = f"{PKG}.providers"
    offenders: dict[str, list[str]] = {}
    for schema in SCHEMA_MODULES:
        name = f"{PKG}.{schema}"
        prefix = name + "."  # cover a schema PACKAGE's whole subtree (e.g. config/)
        schema_mods = {m: p for m, p in mods.items() if m == name or m.startswith(prefix)}
        assert schema_mods, f"schema module {name} vanished"
        refs = [
            t
            for m, path in schema_mods.items()
            for node in _import_nodes(ast.parse(path.read_text()), top_level_only=False)
            for t in _targets(m, path.name == "__init__.py", node, nodeset)
            if t == providers or t.startswith(providers + ".")
        ]
        if refs:
            offenders[schema] = sorted(set(refs))
    assert not offenders, (
        f"schema modules must not import ai_hats.providers (any level): {offenders}"
    )


def test_composition_layer_is_integrator_only():
    """HATS-865 deny-by-default: outside ALLOWED_COMPOSITION_CONSUMERS no module
    may reference the composition layer at ANY level (deferred included,
    TYPE_CHECKING exempt). Bricks receive the ready CompositionPayload from
    the integrator compose seam instead (ADR-0014 Composition rule).
    """
    offenders = _deny_by_default_offenders(FORBIDDEN_COMPOSITION, ALLOWED_COMPOSITION_CONSUMERS)
    assert not offenders, (
        "composition-layer import drift (HATS-865): a non-ALLOWED module "
        "references the composition layer. Cut the import (inject the "
        "CompositionPayload / a DI callable instead) or justify a new "
        "ALLOWED_COMPOSITION_CONSUMERS entry.\n"
        f"offenders: { {k: offenders[k] for k in sorted(offenders)} }"
    )


def _deny_by_default_offenders(
    forbidden_names: tuple[str, ...], allowed_names: tuple[str, ...]
) -> dict[str, list[str]]:
    """Full-AST offender scan: non-ALLOWED modules referencing a FORBIDDEN one
    at any level (deferred included, TYPE_CHECKING exempt)."""
    mods = _modules()
    nodeset = set(mods)
    forbidden = tuple(f"{PKG}.{m}" for m in forbidden_names)
    allowed = tuple(f"{PKG}.{m}" for m in allowed_names)
    offenders: dict[str, list[str]] = {}
    for name, path in mods.items():
        if any(name == a or name.startswith(a + ".") for a in allowed):
            continue
        refs = [
            t
            for node in _import_nodes(ast.parse(path.read_text()), top_level_only=False)
            for t in _targets(name, path.name == "__init__.py", node, nodeset)
            if any(t == f or t.startswith(f + ".") for f in forbidden)
        ]
        if refs:
            offenders[name.removeprefix(PKG + ".")] = sorted(set(refs))
    return offenders


# HATS-866 `test_tracker_never_imports_wt` retired in T16c (HATS-935): the tracker
# FSM left the integrator for `ai_hats_tracker`, where `test_tracker_boundary`
# structurally enforces the wt-free core (`ai_hats_wt` tolerated only in `cli/`).


def test_detector_flags_a_synthetic_cycle():
    """Self-test: the detector must FIRE on a real cycle and stay quiet without
    one — so a green gate above means 'no cycle', not 'detector broken'."""
    assert _find_cycle({"a": {"b"}, "b": {"a"}}) is not None
    assert _find_cycle({"a": {"b"}, "b": {"c"}, "c": set()}) is None
