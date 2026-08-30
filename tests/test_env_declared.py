"""A name shipped code reads is either declared or rostered here, or this is red.

Both mechanisms this repo has for tracking env names already failed, and both
failed OPEN. The registry: the roster in ``constants.py`` was written from the
shipped-hook vocabulary and silently missed ``AI_HATS_E2E_CATALOG_ACK``, which a
repo script reads. The shape rule: ``withheld_from_subagent()`` knows a hatch by
its ``_ACK``/``_OFF``/``_SKIP`` suffix, so ``AI_HATS_SKIP_SELF_LOCATION_GUARD``
(verb at the front) and ``HATS_SKIP_RETRO`` (no ``AI_HATS_`` at all) are not
recognised.

``BUDGETS`` and ``OVERRIDES`` are a registry too, so they fail the same way
unless something turns red on the name they never got. That is this file.

What it holds is GROWTH, the same bargain ``ELSEWHERE`` in
``tests/test_env_homes.py`` strikes: the roster below is today's inventory, not
an endorsement of it. A name nobody declared and nobody rostered is red, so the
next one is an edit under review rather than a literal nobody sees.

What it does NOT hold: which module spells the literal (``test_env_homes.py``),
whether the declared default is the one the code uses (``test_env_budget.py``),
and a name assembled at runtime rather than written down.

comment-length: allow — the two prior failures ARE the justification for this file
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: An env-var name by its shape, as ``test_env_homes`` spells the same rule,
#: plus the prefix-less ``HATS_`` one. A shape rule fails open by construction —
#: ``_read_site_names`` below is what catches a name of an unfamiliar shape.
ENV_NAME = re.compile(r"^(AI_HATS|AGY|CODEX|CLAUDE|RACK|XDG|HATS)_[A-Z0-9_]+$")

SHELL_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Where a name counts as declared. A table under any other name is invisible
#: here, which is red rather than green — add the name, do not widen the rule.
DECLARATION_TABLES = frozenset({"BUDGETS", "OVERRIDES", "HOOK_BUDGETS"})

#: Names shipped code spells that stay undeclared on purpose, by family, each
#: with the reason the family is out. Today's inventory, not an endorsement.
UNDECLARED: dict[str, frozenset[str]] = {
    "a hatch: a bool a person sets once, recognised by its form in constants.py, never tuned": frozenset(
        {
            "AI_HATS_BACKLOG_GATE_OFF",
            "AI_HATS_COMMENT_LINT_OFF",
            "AI_HATS_CONSENT_ACK",
            "AI_HATS_DESTRUCTIVE_ACK",
            "AI_HATS_DOCS_INDEX_ACK",
            "AI_HATS_E2E_CATALOG_ACK",
            "AI_HATS_E2E_CLEAN_TMP",
            "AI_HATS_GATE_BROKEN_ACK",
            "AI_HATS_GIT_GATE_BROKEN_ACK",
            "AI_HATS_LIFETIME_ACK",
            "AI_HATS_MERGE_ACK",
            "AI_HATS_NON_INTERACTIVE",
            "AI_HATS_NO_RAW_DESTRUCTIVE_SKIP",
            "AI_HATS_NO_UPDATE_CHECK",
            "AI_HATS_PLAN_ACK",
            "AI_HATS_PRIVACY_ACK",
            "AI_HATS_RULE_DELIVERY_ACK",
            "AI_HATS_SECURITY_LINT_OFF",
            "AI_HATS_SHARED_STATE_ACK",
            "AI_HATS_SKILL_LINT_ACK",
            "AI_HATS_SKIP_RETIRED_PRUNE",
            "AI_HATS_SKIP_SELF_LOCATION_GUARD",
            "AI_HATS_SMOKE_SKIP",
            "AI_HATS_TICKET_IDS_ACK",
            "AI_HATS_TOOL_HYGIENE_OFF",
            "AI_HATS_WT_ENTRY_OFF",
            "AI_HATS_WT_GATE_OFF",
            "AI_HATS_WT_INTERP_OFF",
            "AI_HATS_YOLO",
            "HATS_SKIP_RETRO",
        }
    ),
    "the spawn envelope: one call's facts, WRITTEN into a child by us or by the surface": frozenset(
        {
            "AGY_TOOL_NAME",
            "AI_HATS_BRANCH_NAME",
            "AI_HATS_BYPASS_JOURNAL",
            "AI_HATS_CONSENT_TICKET",
            "AI_HATS_CONSENT_WRAPPER_CONFIG",
            "AI_HATS_EVENT",
            "AI_HATS_FORCE",
            "AI_HATS_HOOK_CALL",
            "AI_HATS_HOOK_EVENT",
            "AI_HATS_HOOK_POINT",
            "AI_HATS_HOOK_SURFACE_TIMEOUT_MS",
            "AI_HATS_IN_HOOK",
            "AI_HATS_MERGED_SHA",
            "AI_HATS_PTY_IN_FD",
            "AI_HATS_PTY_OUT_FD",
            "AI_HATS_PYTHON",
            "AI_HATS_ROLE",
            "AI_HATS_ROOT_PID",
            "AI_HATS_SESSION_CACHE_DIR",
            "AI_HATS_SESSION_ID",
            "AI_HATS_SESSION_IDENTITY",
            "AI_HATS_TASKS_DIR",
            "AI_HATS_TASK_ID",
            "AI_HATS_WORKTREE_PATH",
        }
    ),
    "install-time: read before there is a project, a venv, or a declaration to read it from": frozenset(
        {
            "AI_HATS_INIT_SRC",
            "AI_HATS_INSTALL_LAUNCHER_URL",
            "AI_HATS_LAUNCHER_DEST",
            "AI_HATS_LAUNCHER_URL",
            "AI_HATS_REPO_URL",
        }
    ),
    "a diagnostic: a developer's tracing switch, not a thing a project configures": frozenset(
        {
            "AI_HATS_DEBUG",
            "AI_HATS_PIPELINE_TRACE",
            "AI_HATS_PIPELINE_TRACE_VALUES",
            "AI_HATS_PTY_RAW_DUMP",
            "AI_HATS_VERBOSE",
        }
    ),
    "a shipped hook's own non-numeric knob: it runs where ai_hats is not installed": frozenset(
        {
            "AI_HATS_RULE_DELIVERY_CMD",
            "AI_HATS_SKILL_LINT_CMD",
            "AI_HATS_TICKET_PREFIX",
            "AI_HATS_WT_GATE_EXTS",
        }
    ),
    "still honoured under the name it had while one surface offered it, and that is all it is for": frozenset(
        {"AI_HATS_AGY_HOOK_TIMEOUT_S"}
    ),
    "the platform's own environment: read, never defined here, no default of ours to state": frozenset(
        {"NO_COLOR", "PATH", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH"}
    ),
    "a switch that exists for a test to hold a run still": frozenset(
        {"AI_HATS_TEST_PAUSE_AFTER_COMPLETE"}
    ),
}


def _rostered() -> set[str]:
    return {name for names in UNDECLARED.values() for name in names}


def _roots(repo: Path) -> tuple[Path, ...]:
    """Every tree that ships: this package, each sibling distribution, scripts."""
    return (repo / "src", *sorted((repo / "packages").glob("*/src")), repo / "scripts")


def _is_test(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_") or path.name == "conftest.py"


def _python_files(repo: Path) -> list[tuple[Path, Path]]:
    return [
        (root, path)
        for root in _roots(repo)
        for path in sorted(root.rglob("*.py"))
        if not _is_test(path)
    ]


def _shell_files(repo: Path) -> list[Path]:
    """``*.sh`` plus the extensionless shims — a surface's hook file has no suffix."""
    found: list[Path] = []
    for root in _roots(repo):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or _is_test(path):
                continue
            if path.suffix == ".sh":
                found.append(path)
            elif not path.suffix and _has_shebang(path):
                found.append(path)
    return found


def _has_shebang(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(2) == b"#!"


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _Module:
    """One shipped python file, read as syntax once."""

    __slots__ = ("name", "path", "tree", "literals", "symbols", "loads")

    def __init__(self, root: Path, path: Path) -> None:
        self.name = _module_name(root, path)
        self.path = path
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.literals: set[str] = set()
        self.symbols: dict[str, tuple] = {}
        self.loads: set[str] = set()
        self._read()

    def _read(self) -> None:
        skip = _export_list_nodes(self.tree)
        for node in ast.walk(self.tree):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if ENV_NAME.match(node.value):
                    self.literals.add(node.value)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.loads.add(node.id)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.symbols[alias.asname or alias.name] = (
                        "import",
                        node.module,
                        node.level,
                        alias.name,
                    )
            if isinstance(node, ast.Assign):
                self._bind(node)

    def _bind(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if ENV_NAME.match(value.value):
                for target in targets:
                    self.symbols[target] = ("literal", value.value)
        elif isinstance(value, ast.Name):
            for target in targets:
                self.symbols[target] = ("alias", value.id)


def _export_list_nodes(tree: ast.AST) -> set[int]:
    """``__all__`` holds PYTHON names, and this repo spells plenty of them in the
    env shape (``AI_HATS_PROJECT_DIR_ENV``, ``CLAUDE_MD_FILENAME``)."""
    skip: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            skip.update(id(sub) for sub in ast.walk(node))
    return skip


def _modules(repo: Path) -> dict[str, _Module]:
    return {
        module.name: module
        for root, path in _python_files(repo)
        for module in [_Module(root, path)]
    }


def _resolve(
    modules: dict[str, _Module],
    module: str,
    name: str,
    seen: frozenset[tuple[str, str]] = frozenset(),
    limit: int | None = None,
) -> str | None:
    """The literal an identifier stands for, across as many hops as it takes.

    ``GIT_HOOK_TIMEOUT_ENV = ENV_GIT_HOOK_TIMEOUT_S = "AI_HATS_GIT_HOOK_TIMEOUT_S"``
    is two hops through two modules; ``limit`` exists so a test can show that
    stopping short loses the reader, never for the guard itself.
    """
    if (module, name) in seen or module not in modules:
        return None
    entry = modules[module].symbols.get(name)
    if entry is None:
        return None
    if entry[0] == "literal":
        return entry[1]
    if limit is not None and limit <= 0:
        return None
    seen = seen | {(module, name)}
    hops = None if limit is None else limit - 1
    if entry[0] == "alias":
        return _resolve(modules, module, entry[1], seen, hops)
    _, imported, level, original = entry
    return _resolve(modules, _import_target(module, imported, level), original, seen, hops)


def _import_target(module: str, imported: str | None, level: int) -> str:
    if not level:
        return imported or ""
    package = module.split(".")[:-level] or [""]
    return ".".join([*package, imported] if imported else package)


def _spelled_names(repo: Path, limit: int | None = None) -> dict[str, set[str]]:
    """Env-shaped names each shipped file spells, itself or through an alias."""
    modules = _modules(repo)
    found: dict[str, set[str]] = {}
    for module in modules.values():
        where = str(module.path.relative_to(repo))
        names = set(module.literals)
        for identifier in module.loads:
            resolved = _resolve(modules, module.name, identifier, limit=limit)
            if resolved:
                names.add(resolved)
        for name in names:
            found.setdefault(name, set()).add(where)
    for path in _shell_files(repo):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in SHELL_REF.finditer(text):
            if ENV_NAME.match(match.group(1)):
                found.setdefault(match.group(1), set()).add(str(path.relative_to(repo)))
    return found


def _is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    return isinstance(node, ast.Name) and node.id in {"environ", "env"}


def _read_key(node: ast.AST) -> ast.AST | None:
    """The expression naming the variable, for every way this repo reads one."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in {"get", "pop", "setdefault"} and _is_environ(node.func.value):
            return node.args[0] if node.args else None
        if node.func.attr == "getenv" and isinstance(node.func.value, ast.Name):
            return node.args[0] if node.args else None
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
        return node.slice
    if isinstance(node, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in node.ops):
        if any(_is_environ(c) for c in node.comparators):
            return node.left
    return None


def _call_args(node: ast.Call) -> dict[int | str, ast.AST]:
    args: dict[int | str, ast.AST] = dict(enumerate(node.args))
    args.update({kw.arg: kw.value for kw in node.keywords if kw.arg})
    return args


def _callee(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    return node.func.attr if isinstance(node.func, ast.Attribute) else None


def _parameters(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int | str]:
    positional = [*func.args.posonlyargs, *func.args.args]
    slots: dict[str, int | str] = {arg.arg: i for i, arg in enumerate(positional)}
    slots.update({arg.arg: arg.arg for arg in func.args.kwonlyargs})
    return slots


def _sinks(modules: dict[str, _Module]) -> set[tuple[str, int | str]]:
    """``(function, argument)`` pairs whose argument ends up naming a variable.

    Found rather than listed: ``tool_home(name, env_var)`` hands its second
    argument to ``tool_home_override``, which hands it to ``_read``, which calls
    ``os.environ.get``. Three hops, and the literal at the end of them is an env
    name whatever it is called — which is how a name no prefix rule expects is
    still caught.
    """
    sinks: set[tuple[str, int | str]] = set()
    edges: list[tuple[str, int | str, str, int | str]] = []
    for module in modules.values():
        for func in ast.walk(module.tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            slots = _parameters(func)
            for node in ast.walk(func):
                key = _read_key(node)
                if isinstance(key, ast.Name) and key.id in slots:
                    sinks.add((func.name, slots[key.id]))
                if isinstance(node, ast.Call) and (callee := _callee(node)):
                    for position, value in _call_args(node).items():
                        if isinstance(value, ast.Name) and value.id in slots:
                            edges.append((func.name, slots[value.id], callee, position))
    growing = True
    while growing:
        growing = False
        for caller, slot, callee, position in edges:
            if (callee, position) in sinks and (caller, slot) not in sinks:
                sinks.add((caller, slot))
                growing = True
    return sinks


def _read_site_names(repo: Path) -> dict[str, set[str]]:
    """Names read as a literal at a read site, of any shape at all."""
    modules = _modules(repo)
    sinks = _sinks(modules)
    found: dict[str, set[str]] = {}

    def keep(node: ast.AST | None, where: str) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if IDENTIFIER.match(node.value):
                found.setdefault(node.value, set()).add(where)

    for module in modules.values():
        where = str(module.path.relative_to(repo))
        for node in ast.walk(module.tree):
            keep(_read_key(node), where)
            if isinstance(node, ast.Call) and (callee := _callee(node)):
                for position, value in _call_args(node).items():
                    if (callee, position) in sinks:
                        keep(value, where)
    return found


def _names(repo: Path) -> dict[str, set[str]]:
    found = _spelled_names(repo)
    for name, where in _read_site_names(repo).items():
        found.setdefault(name, set()).update(where)
    return found


def _declared(repo: Path) -> set[str]:
    modules = _modules(repo)
    declared: set[str] = set()
    for module in modules.values():
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(t, ast.Name) and t.id in DECLARATION_TABLES for t in targets):
                continue
            # By POSITION in the entry, never by the shape of the name: filtering
            # these by prefix is how CLINE_DATA_DIR read as undeclared while
            # sitting in the very tuple that declares it. `Budget` is a call
            # because a reader consults it at runtime; an override is a plain
            # dict because nothing does.
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Call) and sub.args:
                    named = sub.args[0]
                elif isinstance(sub, ast.Dict):
                    named = next(
                        (
                            value
                            for key, value in zip(sub.keys, sub.values)
                            if isinstance(key, ast.Constant) and key.value == "name"
                        ),
                        None,
                    )
                else:
                    continue
                if isinstance(named, ast.Constant) and isinstance(named.value, str):
                    declared.add(named.value)
                elif isinstance(named, ast.Name):
                    if resolved := _resolve(modules, module.name, named.id):
                        declared.add(resolved)
    return declared


def _strays(repo: Path) -> dict[str, set[str]]:
    declared = _declared(repo)
    rostered = _rostered()
    return {
        name: where
        for name, where in _names(repo).items()
        if name not in declared and name not in rostered
    }


def test_every_name_the_shipped_code_reads_is_declared_or_deliberately_not() -> None:
    strays = _strays(REPO)
    report = "; ".join(
        f"{name} ({', '.join(sorted(where)[:3])})" for name, where in sorted(strays.items())
    )
    assert not strays, (
        f"these names are read by shipped code and nothing declares them: {report}. "
        f"Declare each in BUDGETS or OVERRIDES beside its reader, or add it to "
        f"UNDECLARED here with the reason its family stays out. If it IS declared, "
        f"its table is named something this guard does not know — add that name to "
        f"DECLARATION_TABLES rather than widening the rule."
    )


def test_the_roster_carries_nothing_the_declarations_now_cover() -> None:
    """A rostered name that got declared re-opens the hole: the roster goes on
    excusing a name that no longer needs excusing, and stops meaning anything."""
    covered = sorted(_rostered() & _declared(REPO))
    assert covered == [], f"UNDECLARED still excuses names that are now declared: {covered}"


def test_the_roster_carries_nothing_the_code_stopped_spelling() -> None:
    """The stale entry is the dangerous one — it permits a name nobody reads,
    and the next name to arrive under it is invisible."""
    dead = sorted(_rostered() - set(_names(REPO)))
    assert dead == [], f"UNDECLARED names variables no shipped file spells any more: {dead}"


def test_no_name_is_rostered_under_two_reasons() -> None:
    families = list(UNDECLARED.values())
    twice = sorted(
        name
        for i, names in enumerate(families)
        for name in names
        if any(name in other for other in families[i + 1 :])
    )
    assert twice == [], f"one name, two reasons — which one is true? {twice}"


#: One planted name per branch of the walk. A branch nobody plants in reads
#: green over a hole for as long as the hole exists.
PLANTED = {
    "package": ("src/ai_hats/impostor.py", 'NAME = "AI_HATS_PLANTED_IN_SRC"\n'),
    "distribution": ("packages/demo/src/demo/impostor.py", 'NAME = "AI_HATS_PLANTED_IN_PACKAGE"\n'),
    "scripts": ("scripts/impostor.py", 'NAME = "AI_HATS_PLANTED_IN_SCRIPTS"\n'),
    "library shell": (
        "packages/demo/src/demo/hooks/impostor.sh",
        'echo "${AI_HATS_PLANTED_IN_LIBRARY_SHELL}"\n',
    ),
    "scripts shell": ("scripts/impostor.sh", 'echo "${AI_HATS_PLANTED_IN_SCRIPTS_SHELL}"\n'),
    "extensionless shim": (
        "src/ai_hats/surfaces/demo/hooks/Impostor",
        '#!/usr/bin/env bash\necho "${AI_HATS_PLANTED_IN_SHIM}"\n',
    ),
}


@pytest.mark.parametrize("branch", sorted(PLANTED))
def test_the_positive_control_a_planted_name_is_caught_in_every_branch(
    branch: str, tmp_path: Path
) -> None:
    """Without one of these, a walk that visits that branch and parses nothing
    is indistinguishable from a branch with nothing to find."""
    relpath, body = PLANTED[branch]
    planted = tmp_path / relpath
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(body, encoding="utf-8")
    expected = re.search(r"AI_HATS_PLANTED_[A-Z_]+", body).group()
    assert _strays(tmp_path) == {expected: {relpath}}


def test_the_positive_control_a_name_of_an_unfamiliar_shape_is_caught_too(tmp_path: Path) -> None:
    """The shape rule's own failure, made a test: no prefix here is one this repo
    knows, and the read site is what says the literal is a variable name."""
    module = tmp_path / "src" / "pkg" / "reader.py"
    module.parent.mkdir(parents=True)
    module.write_text('import os\n\n\ndef home():\n    return os.environ.get("WIDGET_HOME")\n')
    assert set(_strays(tmp_path)) == {"WIDGET_HOME"}


def test_the_negative_control_a_name_only_mentioned_in_prose_is_not_read(tmp_path: Path) -> None:
    """Every refusal in this repo names the hatch it opens; a text scan would
    have demanded a declaration for each sentence that mentions one."""
    module = tmp_path / "src" / "pkg" / "prose.py"
    module.parent.mkdir(parents=True)
    module.write_text('"""Set AI_HATS_NOT_REAL_ACK=1 to proceed."""\n', encoding="utf-8")
    assert _strays(tmp_path) == {}


def test_an_alias_two_hops_from_its_literal_still_names_its_reader(tmp_path: Path) -> None:
    """``paths/_dirs.py`` reaches ``AI_HATS_DIR`` exactly this way, through a
    re-export in between. A resolver that stops short drops the module that
    reads it and keeps the one that only spells it — and stays green."""
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "env.py").write_text('ENV_BUDGET_S = "AI_HATS_TWO_HOP_S"\n', encoding="utf-8")
    (package / "constants.py").write_text(
        "from .env import ENV_BUDGET_S\n\nBUDGET_ENV = ENV_BUDGET_S\n", encoding="utf-8"
    )
    (package / "reader.py").write_text(
        "from .constants import BUDGET_ENV\n\nprint(BUDGET_ENV)\n", encoding="utf-8"
    )
    reader = "src/pkg/reader.py"
    assert reader in _spelled_names(tmp_path)["AI_HATS_TWO_HOP_S"]
    assert reader not in _spelled_names(tmp_path, limit=1)["AI_HATS_TWO_HOP_S"]


def test_every_root_of_the_walk_reads_files_in_this_repo() -> None:
    """The count, not the names: a distribution that spells none is fine, a root
    the walk never opened is the hole a planted name in one branch cannot see."""
    python = {root: 0 for root in _roots(REPO)}
    for root, _ in _python_files(REPO):
        python[root] += 1
    empty = sorted(str(root.relative_to(REPO)) for root, count in python.items() if not count)
    assert empty == [], f"these shipped roots hold no python this walk could read: {empty}"
    shell = _shell_files(REPO)
    assert [p for p in shell if p.suffix == ".sh"], "the shell walk found no .sh file"
    assert [p for p in shell if not p.suffix], "the shell walk found no extensionless shim"
