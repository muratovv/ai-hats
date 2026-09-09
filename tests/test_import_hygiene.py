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
    "env",  # HATS-1414: centralized env var access leaf
    "paths",  # HATS-862: git_env + safe_delete -> core
    "fs_digest",  # HATS-1217: shared by the port, the sweeper and the legacy sweep
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
    # HATS-1130: ec85f43d moved ClaudeSurface out of `providers` into
    # `surfaces/`. It subclasses Surface and reuses that module's markers, so
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
                    if t != name
                    and not t.startswith(prefix)
                    and t != f"{PKG}.env"  # ignore intra-leaf / base-leaf imports
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
        f"schema modules must not import ai_hats.surface_registry (any level): {offenders}"
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


# ---------------------------------------------------------------------------
# HATS-1606 slice 1: project/config resolution is deny-by-default.
#
# The names below are the acquisition sites — everyone referencing one is
# re-deriving the project instead of receiving it. The pinned offender map is
# the slice's work list: step 7 drains it to the entry points, and the pin
# then holds the door (a new acquisition site = a red test; a drained one =
# a deliberate repin).

RESOLUTION_NAMES = (
    "_project_dir",  # cli/_helpers walk-up (falls back to cwd)
    "default_project_dir",  # retired in step 4 — a revival trips here
    "_is_ai_hats_project",  # the paths-leaf marker check, retired in HATS-1883
    "_read_ai_hats_dir_from_yaml",  # raw config peek, retired in HATS-1883
    "_read_venv_path_from_yaml",  # raw config peek, retired in HATS-1883
    "resolve_root",  # rack's resolver (wait.py) / core's future one
    "find_project_root",  # rack's walk-up
)
CONFIG_FILE_LITERAL = "ai-hats.yaml"

_CORE_SRC = (
    Path(__file__).resolve().parent.parent / "packages" / "ai-hats-core" / "src" / "ai_hats_core"
)


def _resolution_cone() -> list[tuple[str, Path]]:
    """The ai-hats + ai-hats-core cone, module name -> file (tests excluded)."""
    cone = [(name, path) for name, path in _modules().items()]
    for path in sorted(_CORE_SRC.rglob("*.py")):
        if "tests" in path.parts:
            continue
        rel = path.relative_to(_CORE_SRC.parent).with_suffix("")
        name = ".".join(rel.parts).removesuffix(".__init__")
        cone.append((name, path))
    return cone


def _resolution_offenders() -> dict[str, tuple[str, ...]]:
    """Modules referencing an acquisition name or the raw config filename.

    Function-level, not module-level: the names live in modules that also hold
    sanctioned code. A module DEFINING a name is listed too — when the slice
    deletes the definition, the module leaves the pin and that is the point.
    """
    offenders: dict[str, set[str]] = {}
    for name, path in _resolution_cone():
        tree = ast.parse(path.read_text(), filename=str(path))
        hits: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in RESOLUTION_NAMES:
                hits.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in RESOLUTION_NAMES:
                hits.add(node.attr)
            elif (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in RESOLUTION_NAMES
            ):
                hits.add(node.name)
            elif isinstance(node, ast.ImportFrom):
                hits.update(a.name for a in node.names if a.name in RESOLUTION_NAMES)
            elif isinstance(node, ast.Constant) and node.value == CONFIG_FILE_LITERAL:
                hits.add(CONFIG_FILE_LITERAL)
        if hits:
            offenders[name] = tuple(sorted(hits))
    return offenders


# Pinned 2026-09-05 after the HATS-1883 contract (21 -> 5 modules). What remains
# is the owners and the two rack-sanctioned copies.
EXPECTED_RESOLUTION_OFFENDERS: dict[str, tuple[str, ...]] = {
    "ai_hats.cli._entry": ("resolve_root",),  # THE sanctioned factory (composition root)
    "ai_hats.cli.wait": (
        "resolve_root",
    ),  # rack's copy; semantics now match core, parity by conformance
    "ai_hats.paths.constants": ("ai-hats.yaml",),  # the literal's one legitimate home
    "ai_hats.rack_cli_provider": ("find_project_root",),
    "ai_hats_core.layout": ("ai-hats.yaml", "resolve_root"),  # the owner
}


def test_project_resolution_is_deny_by_default():
    """No module acquires the project outside the pinned list (HATS-1606).

    Shrinking the pin is progress; growing it is a new re-derivation site and
    needs this test edited in review — that friction is the mechanism.
    """
    actual = _resolution_offenders()
    expected = EXPECTED_RESOLUTION_OFFENDERS
    grown = {m: n for m, n in actual.items() if m not in expected or set(n) - set(expected[m])}
    drained = {
        m: n for m, n in expected.items() if m not in actual or set(n) - set(actual.get(m, ()))
    }
    assert actual == expected, (
        f"project-resolution pin drifted.\nNEW acquisition sites (deny-by-default): {grown}\n"
        f"DRAINED (update the pin, keep the ratchet tight): {drained}"
    )


# ---------------------------------------------------------------------------
# Slice-1 tail: three negative universals, each red on the tree it was written
# against (see the work log of the card that added them) before its fix landed.
#
# Edge counting below INCLUDES ``TYPE_CHECKING`` and deferred imports (ADR-0026
# D5, the F4 ruling): a boundary blind to them is blind to two edges in five.

REPO_ROOT = SRC.parent.parent
_OBSERVE_SRC = REPO_ROOT / "packages" / "ai-hats-observe" / "src" / "ai_hats_observe"
_OBSERVE_TESTS = REPO_ROOT / "packages" / "ai-hats-observe" / "tests"
_INTEGRATOR_TESTS = REPO_ROOT / "tests"

CLI = f"{PKG}.cli"


def _all_import_nodes(tree: ast.AST):
    """Every Import/ImportFrom — module-level, deferred AND under TYPE_CHECKING."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def _cli_inbound_edges() -> dict[str, tuple[str, ...]]:
    """Non-``cli`` modules that import anything under ``ai_hats.cli``.

    ``cli`` is the top of the tree — the composition root and the process entry
    points live there. A deep module importing it is reaching UP for something it
    should have received (R2); an import of a private ``_name`` is that, twice.
    """
    mods = _modules()
    nodeset = set(mods)
    edges: dict[str, set[str]] = {}
    for name, path in mods.items():
        if name == CLI or name.startswith(CLI + "."):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        hits = {
            t
            for node in _all_import_nodes(tree)
            for t in _targets(name, path.name == "__init__.py", node, nodeset)
            if t == CLI or t.startswith(CLI + ".")
        }
        if hits:
            edges[name] = hits
    return {m: tuple(sorted(h)) for m, h in sorted(edges.items())}


# The work list: every module reaching up into cli, with what it takes. Shrinks
# only. ``__main__`` is the process entry point and stays; the rest are the
# epic's "inbound edges into cli -> 0" metric, one card at a time.
EXPECTED_CLI_INBOUND: dict[str, tuple[str, ...]] = {
    "ai_hats.__main__": ("ai_hats.cli", "ai_hats.cli._helpers"),
    "ai_hats._bump_internal": ("ai_hats.cli._entry", "ai_hats.cli.assembly"),  # process entry point
    "ai_hats.consent_mcp.server": (
        "ai_hats.cli._entry",
    ),  # the consent MCP server, a process entry point
    "ai_hats.assembler": ("ai_hats.cli.maintenance",),
    "ai_hats.channel": ("ai_hats.cli.maintenance",),
    "ai_hats.pipeline.steps.handoff": ("ai_hats.cli.reflect",),
    "ai_hats.rack_cli_provider": ("ai_hats.cli._entry",),  # the rack-side process entry point
    "ai_hats.pipeline.steps.maybe_spawn_session_reviewer": (
        "ai_hats.cli",
        "ai_hats.cli._entry",
        "ai_hats.cli.reflect_session_main",
    ),
    "ai_hats.retro.auto_retro": ("ai_hats.cli._entry",),
}


def test_no_new_module_reaches_up_into_cli():
    actual = _cli_inbound_edges()
    expected = EXPECTED_CLI_INBOUND
    grown = {m: n for m, n in actual.items() if m not in expected or set(n) - set(expected[m])}
    drained = {
        m: n for m, n in expected.items() if m not in actual or set(n) - set(actual.get(m, ()))
    }
    assert actual == expected, (
        f"cli-inbound pin drifted.\nNEW edges into cli (deny-by-default): {grown}\n"
        f"DRAINED (update the pin, keep the ratchet tight): {drained}\n"
        f"paste-ready pin:\nEXPECTED_CLI_INBOUND = {actual!r}"
    )


_HOST_MODULES = ("_host", "_seam")  # observe's host module, and the name it retired


def _names_a_store_on(node: ast.AST, aliases: frozenset[str] = frozenset()) -> str | None:
    """The base name of ``<base>.<attr> = …`` / ``setattr(<base>, …)`` when it is a host module.

    ``aliases`` are the names the file bound the module to (``import … as``).
    """
    names = set(_HOST_MODULES) | set(aliases)
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute):
                base = t.value
                if isinstance(base, ast.Name) and base.id in names:
                    return base.id
                if isinstance(base, ast.Attribute) and base.attr in names:
                    return base.attr
    if isinstance(node, ast.Call):
        fn = node.func
        is_setattr = (isinstance(fn, ast.Name) and fn.id == "setattr") or (
            isinstance(fn, ast.Attribute) and fn.attr == "setattr"
        )
        if is_setattr and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id in names:
                return first.id
            if isinstance(first, ast.Attribute) and first.attr in names:
                return first.attr
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if any(
                    first.value.endswith(f".{m}") or f".{m}." in first.value for m in _HOST_MODULES
                ):
                    return first.value
    return None


def _host_stores_outside_the_module() -> dict[str, list[int]]:
    """Every place that assigns INTO observe's host module from outside it.

    ``attach()`` is the one writer; a store from anywhere else — the integrator's
    mount, a test's monkeypatch — is the implicit wiring this gate retires.
    """
    stores: dict[str, list[int]] = {}
    roots = (SRC, _INTEGRATOR_TESTS, _OBSERVE_SRC, _OBSERVE_TESTS)
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.stem in _HOST_MODULES:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            aliases = frozenset(
                a.asname or a.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for a in node.names
                if a.name in _HOST_MODULES
            )
            lines = [n.lineno for n in ast.walk(tree) if _names_a_store_on(n, aliases) is not None]
            if lines:
                stores[str(path.relative_to(REPO_ROOT))] = lines
    return stores


def test_nothing_assigns_into_observes_host_module():
    assert _host_stores_outside_the_module() == {}, (
        "observe's host is attached, never assigned into — use "
        f"ai_hats_observe.cli.attach(Host(...)):\n{_host_stores_outside_the_module()}"
    )


def test_host_store_detector_fires_on_a_synthetic_store():
    """Self-test: the detector must FIRE on each spelling it claims to catch."""
    for src in (
        "_host._CONSOLE = c",
        "_seam._LAYOUT = f",
        "cli._host._CONSOLE = c",
        "setattr(_host, '_CONSOLE', c)",
        "monkeypatch.setattr(_seam, '_LAYOUT', f)",
        "monkeypatch.setattr('ai_hats_observe.cli._host._CONSOLE', c)",
    ):
        assert any(_names_a_store_on(n) for n in ast.walk(ast.parse(src))), src
    aliased = "from ai_hats_observe.cli import _seam as _observe_seam\n_observe_seam._LAYOUT = f"
    assert any(
        _names_a_store_on(n, frozenset({"_observe_seam"})) for n in ast.walk(ast.parse(aliased))
    )
    assert not any(_names_a_store_on(n) for n in ast.walk(ast.parse("host().console.print(x)")))


# Every ``f(project_dir) -> Path`` the paths leaf used to derive from a directory it
# had to re-read the environment and the yaml for. ``ProjectLayout`` is the home of
# each; a caller spelling the function, or the leaf growing it back, is re-deriving
# a value it should hold.
PATHS_DERIVATIONS = (
    "ai_hats_dir",
    "ensure_ai_hats_dir",
    "traces_dir",
    "pipeline_steps_dir",
    "sessions_dir",
    "runs_dir",
    "retros_dir",
    "audits_dir",
    "handoffs_dir",
    "worktrees_dir",
    "tracker_dir",
    "backlog_dir",
    "tasks_dir",
    "proposals_dir",
    "hypotheses_dir",
    "hypotheses_flat_dir",
    "decisions_dir",
    "state_md_path",
    "cache_home",
    "project_key",
    "cache_root",
    "session_cache_root",
    "session_cache_dir",
    "worktree_checkouts_dir",
    "library_dir",
    "rules_dir",
    "skills_dir",
    "hooks_dir",
    "user_hooks_dir",
    "user_rules_dir",
    "last_backup_path",
    "venv_path",
    "versions_root",
    "version_dir",
    "current_pointer",
    "complete_sentinel",
    "is_complete",
    "is_usable_version",
    "read_current_sha",
)
PATHS = f"{PKG}.paths"


def _paths_derivation_sites() -> dict[str, tuple[str, ...]]:
    """module -> the derivations it imports from ``ai_hats.paths`` and uses.

    Bound to the import, not to the bare name: ``Surface.rules_dir`` and a local
    ``venv_path`` variable are not derivations, an ``from ..paths import tasks_dir``
    is. ``paths`` itself is the definition site and is not a caller.
    """
    mods = _modules()
    sites: dict[str, set[str]] = {}
    for name, path in mods.items():
        if name == PATHS or name.startswith(PATHS + "."):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        is_pkg = path.name == "__init__.py"
        imported: set[str] = set()
        module_aliases: set[str] = set()
        for node in _all_import_nodes(tree):
            if isinstance(node, ast.ImportFrom):
                base = _targets(name, is_pkg, node, set(mods) | {PATHS})
                if any(b == PATHS or b.startswith(PATHS + ".") for b in base):
                    for a in node.names:
                        if a.name in PATHS_DERIVATIONS:
                            imported.add(a.asname or a.name)
                        elif a.name == "paths" and node.module in (PKG, None):
                            module_aliases.add(a.asname or a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == PATHS:
                        module_aliases.add(a.asname or a.name.split(".")[-1])
        hits: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in imported:
                hits.add(node.id)
            elif (
                isinstance(node, ast.Attribute)
                and node.attr in PATHS_DERIVATIONS
                and isinstance(node.value, ast.Name)
                and node.value.id in module_aliases
            ):
                hits.add(node.attr)
        if hits:
            sites[name.removeprefix(PKG + ".")] = hits
    return {m: tuple(sorted(h)) for m, h in sorted(sites.items())}


# Empty since HATS-1883: the names left the leaf. The pin stays so a module
# importing a revived one is red, and the test refuses the revival itself.
EXPECTED_PATHS_DERIVATION_SITES: dict[str, tuple[str, ...]] = {}


def test_no_new_module_derives_a_path_from_project_dir():
    import ai_hats.paths as paths_leaf

    revived = sorted(n for n in PATHS_DERIVATIONS if hasattr(paths_leaf, n))
    assert not revived, f"a derivation came back to the paths leaf: {revived}"
    actual = _paths_derivation_sites()
    expected = EXPECTED_PATHS_DERIVATION_SITES
    grown = {m: n for m, n in actual.items() if m not in expected or set(n) - set(expected[m])}
    drained = {
        m: n for m, n in expected.items() if m not in actual or set(n) - set(actual.get(m, ()))
    }
    assert actual == expected, (
        f"paths-derivation pin drifted.\nNEW derivation sites (deny-by-default): {grown}\n"
        f"DRAINED (update the pin, keep the ratchet tight): {drained}\n"
        f"paste-ready pin:\nEXPECTED_PATHS_DERIVATION_SITES = {actual!r}"
    )
