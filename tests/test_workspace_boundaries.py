"""HATS-869 — workspace-boundary import-lint (the ADR-0014 dependency rule).

Enforces ``ai-hats -> packages -> ai-hats-core`` for every uv-workspace member:
(1) a member imports only stdlib / intra-package / its *declared* deps (full
AST walk — deferred and TYPE_CHECKING imports count); (2) the declared
first-party graph respects the tiers, so a forbidden edge cannot be legalised
by declaring it; (3) the integrator imports a member only if it declares it.
``pyproject.toml`` is the single source of truth — members discovered from
``[tool.uv.workspace]``, allowlists derived, never hand-copied (HATS-869
plan.md; the wt-local test_boundary.py stays as the git-split guard).
"""
# comment-length: allow — deliberate three-rule contract docstring

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

# smoke: rides the pre-push master wall (-m "(integration or smoke)...") — a
# boundary break must be caught BEFORE the push, not by CI after (HATS-869).
pytestmark = pytest.mark.smoke

ROOT = Path(__file__).resolve().parent.parent
CORE = "ai-hats-core"
INTEGRATOR = "ai-hats"


def _dep_name(spec: str) -> str:
    """PEP 508 distribution name: the leading name token of a dependency spec."""
    match = re.match(r"[A-Za-z0-9._-]+", spec.strip())
    assert match, f"unparseable dependency spec: {spec!r}"
    return match.group(0)


# Dist name -> import root only where they diverge (HATS-933: ai-hats-tracker is
# the first member to declare pyyaml, whose import name is `yaml`). A minimal
# exception list, not a full mapping table (the plan's kill-criteria).
_DIST_IMPORT_OVERRIDES = {"pyyaml": "yaml"}


def _import_root(dist_name: str) -> str:
    # ai-hats-core -> ai_hats_core; diverging names go through the override map.
    override = _DIST_IMPORT_OVERRIDES.get(dist_name.lower())
    if override is not None:
        return override
    return dist_name.replace("-", "_").replace(".", "_")


def _project(pyproject: Path) -> dict:
    return tomllib.loads(pyproject.read_text())["project"]


def _members() -> dict[str, dict]:
    """dist-name -> {src, deps} for every member named by [tool.uv.workspace]."""
    root_cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    members: dict[str, dict] = {}
    for pattern in root_cfg["tool"]["uv"]["workspace"]["members"]:
        for member_dir in sorted(ROOT.glob(pattern)):
            pyproject = member_dir / "pyproject.toml"
            if not pyproject.is_file():
                continue
            project = _project(pyproject)
            optional = project.get("optional-dependencies", {})
            members[project["name"]] = {
                "src": member_dir / "src" / _import_root(project["name"]),
                "deps": [_dep_name(s) for s in project.get("dependencies", [])],
                "optional_deps": [
                    _dep_name(s) for specs in optional.values() for s in specs
                ],
                # HATS-956: surface plugins (packages/surfaces/*) are a consumer
                # tier ABOVE the integrator — the Provider ABC is integrator-bound
                # (ADR-0014 P0#4), so a surface may depend UP on `ai-hats`.
                "is_surface": "surfaces" in member_dir.relative_to(ROOT).parts,
            }
    return members


def _top_level_import_roots(tree: ast.Module) -> set[str]:
    """Top-level package of every absolute import anywhere in the tree.

    Relative imports are intra-package by construction and skipped.
    """
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _boundary_offenders(src: Path, allowed: set[str]) -> dict[str, list[str]]:
    """file -> forbidden import roots, for every module under ``src``."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(src.rglob("*.py")):
        roots = _top_level_import_roots(ast.parse(path.read_text()))
        bad = sorted(
            r for r in roots if r not in allowed and r not in sys.stdlib_module_names
        )
        if bad:
            offenders[str(path.relative_to(src))] = bad
    return offenders


# --------------------------------------------------------------------------- #


def test_members_discovered():
    """Glob sanity: green must mean 'checked and clean', never 'matched nothing'.

    core + wt are the floor as of HATS-885; new members only ever add to it.
    """
    members = _members()
    assert {CORE, "ai-hats-wt"} <= set(members), sorted(members)
    for name, member in members.items():
        assert member["src"].is_dir(), f"{name}: src dir missing at {member['src']}"


def test_member_imports_only_declared_deps():
    """Rule 1: a member imports only stdlib / intra-package / declared deps."""
    offenders: dict[str, dict[str, list[str]]] = {}
    for name, member in _members().items():
        # A soft, guarded import of an optional extra is legal (e.g. tracker's wt
        # extra — HATS-934); Rule 2's tier check stays on hard deps only.
        declared = member["deps"] + member["optional_deps"]
        allowed = {_import_root(name)} | {_import_root(d) for d in declared}
        bad = _boundary_offenders(member["src"], allowed)
        if bad:
            offenders[name] = bad
    assert not offenders, (
        "workspace members may import only stdlib + their declared dependencies "
        f"(declare it in the member pyproject or cut the edge): {offenders}"
    )


def _topology_offenders(
    declared: dict[str, list[str]], surfaces: frozenset[str] = frozenset()
) -> list[str]:
    """Tier violations in the declared first-party dep graph, as edge strings.

    core declares no first-party; a module declares at most core; a surface
    (packages/surfaces/*) may declare the integrator plus any module below it
    (HATS-960: observe for the TranscriptParser base) but never another surface;
    the integrator may declare any module (no module declares the integrator).
    """
    modules = set(declared) - surfaces - {CORE, INTEGRATOR}
    first_party = set(declared) | {INTEGRATOR}
    limits = {
        name: ({CORE, INTEGRATOR} | modules if name in surfaces else {CORE})
        for name in declared
    }
    limits[CORE] = set()
    limits[INTEGRATOR] = set(declared)
    return [
        f"{name} -> {dep}"
        for name, deps in declared.items()
        for dep in deps
        if dep in first_party and dep not in limits[name]
    ]


def test_declared_first_party_topology():
    """Rule 2: the declared dep graph itself respects the ADR-0014 tiers."""
    members = _members()
    declared = {name: member["deps"] for name, member in members.items()}
    declared[INTEGRATOR] = [
        _dep_name(s) for s in _project(ROOT / "pyproject.toml")["dependencies"]
    ]
    surfaces = frozenset(n for n, m in members.items() if m["is_surface"])
    problems = _topology_offenders(declared, surfaces)
    assert not problems, f"ADR-0014 tier violations in declared deps: {problems}"


def test_topology_detector_self_test():
    """Fires on core->package, package->package, and package->integrator edges;
    quiet on the legal shape."""
    legal = {CORE: [], "ai-hats-x": [CORE, "filelock"], INTEGRATOR: ["ai-hats-x"]}
    assert _topology_offenders(legal) == []

    # HATS-956: a surface plugin MAY depend up on the integrator; the SAME edge
    # stays a violation for a plain module (only the surface blessing legalises it).
    surface = {CORE: [], "ai-hats-cline": [CORE, INTEGRATOR], INTEGRATOR: []}
    assert _topology_offenders(surface, frozenset({"ai-hats-cline"})) == []
    assert _topology_offenders(surface) == ["ai-hats-cline -> ai-hats"]

    # HATS-960: a surface may depend DOWN on a module (observe) whose contract it
    # implements; a surface->surface edge stays forbidden.
    surf_mod = {
        CORE: [],
        "ai-hats-observe": [CORE],
        "ai-hats-cline": [CORE, INTEGRATOR, "ai-hats-observe"],
        "ai-hats-other": [CORE, "ai-hats-cline"],
        INTEGRATOR: ["ai-hats-observe"],
    }
    surfs = frozenset({"ai-hats-cline", "ai-hats-other"})
    assert _topology_offenders(surf_mod, surfs) == ["ai-hats-other -> ai-hats-cline"]

    rogue = {
        CORE: ["ai-hats-x"],
        "ai-hats-x": ["ai-hats-y"],
        "ai-hats-y": [INTEGRATOR],
        INTEGRATOR: ["ai-hats-x"],
    }
    assert set(_topology_offenders(rogue)) == {
        f"{CORE} -> ai-hats-x",
        "ai-hats-x -> ai-hats-y",
        f"ai-hats-y -> {INTEGRATOR}",
    }


def test_integrator_imports_only_declared_members():
    """Rule 3: the integrator imports a member only if it declares it.

    Third-party discipline for the integrator is out of scope (plan: no
    dist->import name-mapping table) — only member edges are policed.
    """
    members = _members()
    declared = {_dep_name(s) for s in _project(ROOT / "pyproject.toml")["dependencies"]}
    undeclared_roots = {_import_root(n) for n in members if n not in declared}
    offenders: dict[str, list[str]] = {}
    for path in sorted((ROOT / "src" / "ai_hats").rglob("*.py")):
        roots = _top_level_import_roots(ast.parse(path.read_text()))
        bad = sorted(r for r in roots if r in undeclared_roots)
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "the integrator imports workspace members it does not declare in "
        f"[project.dependencies]: {offenders}"
    )


def test_boundary_detector_self_test():
    """The detector FIRES on an undeclared root and stays quiet on the allowed
    set — a green rule 1 means 'boundary clean', not 'detector broken'."""
    tree = ast.parse(
        "import os\n"
        "from ai_hats_core import atomic_write_text\n"
        "def late():\n"
        "    from ai_hats.models import TaskCard\n"  # deferred — must still count
        "from .sibling import helper\n"  # relative — intra-package, skipped
    )
    roots = _top_level_import_roots(tree)
    assert "ai_hats" in roots and "sibling" not in roots
    allowed = {"ai_hats_core", "pkg_under_test"}
    bad = {r for r in roots if r not in allowed and r not in sys.stdlib_module_names}
    assert bad == {"ai_hats"}
