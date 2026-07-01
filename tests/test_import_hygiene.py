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
# ANY level (incl. deferred / TYPE_CHECKING). NB: `models` is intentionally NOT
# here — it has real, deferred first-party deps (paths, providers), so it is not
# a leaf. Keeping the list honest is what the gate enforces.
LEAF_MODULES = ("constants", "paths", "safe_delete")

# ADR-0013 D6 — the hook-agnostic wt core must not import ai-hats accretions
# (one-directional rule: ai_hats.* -> wt core, never back). Keyed on the wt/
# sub-package with the module-or-subtree idiom (see test_leaf_modules_are_pure):
# the single key ("wt",) covers every module under ai_hats.wt (manager, locks,
# __init__) — HATS-851 moved the engine here from the flat worktree[_locks] files.
WT_CORE_MODULES = ("wt",)
WT_FORBIDDEN_ACCRETIONS = (
    "paths",
    "models",
    "assembler",
    "composer",
    "materialize",
    "state",
    "worktree_hooks",
)


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


def test_detector_flags_a_synthetic_cycle():
    """Self-test: the detector must FIRE on a real cycle and stay quiet without
    one — so a green gate above means 'no cycle', not 'detector broken'."""
    assert _find_cycle({"a": {"b"}, "b": {"a"}}) is not None
    assert _find_cycle({"a": {"b"}, "b": {"c"}, "c": set()}) is None


def _forbidden_hits(targets: list[str], forbidden_roots: tuple[str, ...]) -> list[str]:
    """Targets that match a forbidden root exactly or as a package subtree."""
    return [t for t in targets if any(t == r or t.startswith(r + ".") for r in forbidden_roots)]


def test_wt_core_imports_no_accretions():
    """ADR-0013 D6: the wt core imports no ai-hats accretion.

    The hook-agnostic engine (the ``wt`` package: ``wt.manager`` + ``wt.locks``)
    must not import
    ``{paths, models, assembler, composer, materialize, state, worktree_hooks}``
    — the one-directional boundary that prevents accretion creep back into core
    (the HATS-715 regression class). Full walk (incl. deferred imports, ignoring
    TYPE_CHECKING); intra-core ``wt.manager <-> wt.locks`` and leaf helpers
    (e.g. ``ai_hats_core.atomic_io``) stay allowed.

    RED-under-revert: re-add ``from ai_hats.paths import worktrees_dir`` to either
    core module and this fails. (Use the *absolute* form — a relative ``.paths``
    inside ``wt/`` resolves to the non-existent ``ai_hats.wt.paths``, so only
    ``ai_hats.paths`` / ``..paths`` reaches the real accretion.)
    """
    mods = _modules()
    nodeset = set(mods)
    forbidden_roots = tuple(f"{PKG}.{d}" for d in WT_FORBIDDEN_ACCRETIONS)
    offenders: dict[str, list[str]] = {}
    for core in WT_CORE_MODULES:
        name = f"{PKG}.{core}"
        prefix = name + "."  # the core's own subtree (empty for a plain module)
        core_mods = {m: p for m, p in mods.items() if m == name or m.startswith(prefix)}
        hits: list[str] = []
        for m, path in core_mods.items():
            tree = ast.parse(path.read_text())
            for node in _import_nodes(tree, top_level_only=False):
                targets = _targets(m, path.name == "__init__.py", node, nodeset)
                hits += _forbidden_hits(targets, forbidden_roots)
        if hits:
            offenders[core] = sorted(set(hits))
    assert not offenders, (
        "wt core (ADR-0013 D6) must not import ai-hats accretions "
        f"{list(WT_FORBIDDEN_ACCRETIONS)}: {offenders}"
    )


def test_wt_core_lint_self_test():
    """Self-test: the accretion detector FIRES on a synthetic forbidden import
    and stays quiet on an allowed intra-core one — so a green gate above means
    'core is clean', not 'detector broken'."""
    roots = (f"{PKG}.paths",)
    nodeset = {f"{PKG}.paths", f"{PKG}.wt.locks"}

    def offends(source: str) -> bool:
        tree = ast.parse(source)
        for node in _import_nodes(tree, top_level_only=False):
            targets = _targets(f"{PKG}.wt.manager", False, node, nodeset)
            if _forbidden_hits(targets, roots):
                return True
        return False

    # Post-move: a wt/ module is at ai_hats.wt.manager, so a relative reach for the
    # accretion needs `..paths` (double-dot); single-dot `.paths` would resolve to
    # the non-existent ai_hats.wt.paths (the move trap, HATS-851).
    assert offends("from ..paths import worktrees_dir")  # forbidden (relative)
    assert offends("import ai_hats.paths")  # forbidden (absolute)
    assert offends("def f():\n    from ..paths import worktrees_dir")  # deferred too
    assert not offends("from .locks import _state_key")  # allowed intra-core
