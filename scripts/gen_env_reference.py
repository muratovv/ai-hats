#!/usr/bin/env python3
"""Render docs/reference-env.md from the declarations the running code reads.

Every row's number is taken from the one place the code takes it from, never
agreed with: `BUDGETS` and `OVERRIDES` in `src/ai_hats/env.py` for what imports
`ai_hats`, and the CALL SITE itself for the four budgets living in hooks that
are copied into a consuming project — those hooks cannot import `ai_hats`, so
there is nothing for them to share, and reading their literal by AST is what
keeps the page from becoming the number's second home.

What `--check` CANNOT catch is a doc sentence drifting from its own code: the
name, type and default are derived, the last column is prose and rots green.
The claim is "this view is current", never "these sentences are true". It is
also silent about a name the code reads and nobody declared — that guard is
`tests/test_env_declared.py`, not this one.

comment-length: allow
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CHECK = "env-reference"
PAGE_RELPATH = "docs/reference-env.md"

LIBRARY_SRC = "packages/ai-hats-library/src/ai_hats_library"


class SourceError(Exception):
    """A declared source that cannot be read as a row."""


@dataclass(frozen=True)
class Row:
    name: str
    type: str
    default: str
    doc: str
    pin: str = ""  # what a spawner writes under this name, when it is a pin too


@dataclass(frozen=True)
class HookBudget:
    """A budget whose home is its own call site, because it has no other one.

    The file ships into a project that does not have `ai_hats` installed, so the
    literal in the call is the only copy there can be. `where` names it for a
    reader; the number is never written here.
    """

    name: str
    relpath: str
    doc: str


HOOK_BUDGETS: tuple[HookBudget, ...] = (
    HookBudget(
        "AI_HATS_COMMENT_MAX_LINES",
        f"{LIBRARY_SRC}/usage/skills/comment-length-lint/hooks/comment_length_lint.py",
        "Consecutive standalone `#` comment lines allowed before the edit-time lint speaks up.",
    ),
    HookBudget(
        "AI_HATS_DOCSTRING_MAX_LINES",
        f"{LIBRARY_SRC}/usage/skills/comment-length-lint/hooks/comment_length_lint.py",
        "Lines a docstring may run to before that same lint flags it.",
    ),
    HookBudget(
        "AI_HATS_DOCSTRING_MAX_CHARS",
        f"{LIBRARY_SRC}/usage/skills/comment-length-lint/hooks/comment_length_lint.py",
        "Characters a docstring may run to before that same lint flags it.",
    ),
    HookBudget(
        "AI_HATS_GATE_MARKER_KEEP_DAYS",
        f"{LIBRARY_SRC}/ai-hats-dev/skills/maintainer-quality-gate/lib/gate-marker.sh",
        "Days a quality-gate marker survives the sweep before it is deleted.",
    ),
)


def _dotted(relpath: str) -> str:
    """The module's real import name, so a relative import inside it resolves."""
    parts = relpath[: -len(".py")].split("/")
    return ".".join(parts[parts.index("src") + 1 :]) if "src" in parts else parts[-1]


def _load_module(path: Path, relpath: str):
    """Execute a declaration module from its file, so a copy can be read too."""
    dotted = _dotted(relpath)
    spec = importlib.util.spec_from_file_location(dotted, path)
    if spec is None or spec.loader is None:
        raise SourceError(f"{relpath}: cannot be loaded as a module")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec, because a `@dataclass` and a relative import both
    # resolve through `sys.modules`; put back after, so exec'ing a COPY of a
    # module cannot leave the process holding it under the real name.
    previous = sys.modules.get(dotted)
    sys.modules[dotted] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise SourceError(f"{relpath}: cannot execute: {exc}") from exc
    finally:
        if previous is None:
            sys.modules.pop(dotted, None)
        else:
            sys.modules[dotted] = previous
    return module


#: A declaration is recognised by SHAPE — a module-level `BUDGETS` or `OVERRIDES`
#: anywhere in the workspace. A roster of modules fails open, the way the
#: bypass-flag roster beside it already did.
_DECLARES = re.compile(r"^(?:BUDGETS|OVERRIDES)\b", re.MULTILINE)
_SEARCH_ROOTS = ("src", "packages")
_SKIP_PARTS = frozenset({"tests", "__pycache__", ".venv", "node_modules", "build", "dist"})


def declaration_modules(root: Path) -> list[str]:
    """Every module in the workspace that declares knobs, main distribution first."""
    found = []
    for top in _SEARCH_ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            relpath = path.relative_to(root).as_posix()
            # Against the RELATIVE parts: an absolute one carries whatever the
            # checkout happens to sit under, which is not the tree's own shape.
            if _SKIP_PARTS.intersection(relpath.split("/")):
                continue
            if _DECLARES.search(path.read_text(encoding="utf-8", errors="ignore")):
                found.append(relpath)
    # The package a reader starts from comes first; the siblings that cannot
    # import it follow, in one deterministic order.
    return sorted(found, key=lambda rel: (not rel.startswith("src/"), rel))


def py_call_default(src: str, name: str, where: str) -> int:
    """The int literal a call passes beside the string `name`.

    Matched by the name argument rather than by the helper's identifier: renaming
    `_env_int` must not silently drop the row it feeds.
    """
    found: set[int] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        raise SourceError(f"{where}: cannot parse: {exc}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        first, second = node.args[0], node.args[1]
        if not (isinstance(first, ast.Constant) and first.value == name):
            continue
        if not (isinstance(second, ast.Constant) and isinstance(second.value, int)):
            raise SourceError(f"{where}: {name} is read with a non-literal default")
        found.add(second.value)
    return _one(found, name, where)


_SH_DEFAULT = r"\$\{%s:-([0-9]+)\}"


def sh_default(src: str, name: str, where: str) -> int:
    """The int literal in `${NAME:-N}`, the shell spelling of the same thing."""
    found = {int(m) for m in re.findall(_SH_DEFAULT % re.escape(name), src)}
    return _one(found, name, where)


def _one(found: set[int], name: str, where: str) -> int:
    if not found:
        raise SourceError(
            f"{where}: no call reads {name} with a literal default — "
            "the row has no source (was the call site moved or rewritten?)"
        )
    if len(found) > 1:
        raise SourceError(
            f"{where}: {name} is read with {len(found)} different defaults {sorted(found)}"
        )
    return found.pop()


def _cell(value: object) -> str:
    text = "—" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _override_row(declaration: object) -> Row:
    """One declared path. A sentinel is part of the TYPE — `-` is a switch, not a path."""
    kind, doc = "path", _cell(declaration.doc)
    sentinel = getattr(declaration, "sentinel", None)
    if sentinel:
        literal, effect = sentinel
        kind = f"path \\| `{literal}`"
        doc = f"{doc} Set to `{literal}` to {_cell(effect)}."
    return Row(
        _cell(declaration.name),
        kind,
        _cell(declaration.default) or "—",
        doc,
        _cell(getattr(declaration, "pin", None) or ""),
    )


def _hook_budget_rows(root: Path) -> list[Row]:
    """The four budgets whose default is read back out of their own call site."""
    rows = []
    for spec in HOOK_BUDGETS:
        path = root / spec.relpath
        if not path.exists():
            raise SourceError(f"{spec.relpath}: gone — {spec.name} has no call site to read")
        src = path.read_text(encoding="utf-8")
        reader = sh_default if path.suffix == ".sh" else py_call_default
        rows.append(Row(spec.name, "int", _cell(reader(src, spec.name, spec.relpath)), spec.doc))
    return rows


def collect(root: Path) -> tuple[list[Row], list[Row], list[Row]]:
    """(budgets, our paths, honoured foreign) as the declarations spell them."""
    modules = declaration_modules(root)
    if not modules:
        raise SourceError(
            f"nothing under {root} declares BUDGETS or OVERRIDES — every knob "
            "would vanish from the page and the page would still render"
        )
    budgets: list[Row] = []
    ours: list[Row] = []
    foreign: list[Row] = []
    for relpath in modules:
        module = _load_module(root / relpath, relpath)
        budgets += [
            Row(b.name, type(b.default).__name__, _cell(b.default), _cell(b.doc))
            for b in getattr(module, "BUDGETS", ())
        ]
        for declared in getattr(module, "OVERRIDES", ()):
            row = _override_row(declared)
            (foreign if getattr(declared, "foreign", False) else ours).append(row)
    return budgets + _hook_budget_rows(root), ours, foreign


def _table(rows: list[Row]) -> list[str]:
    out = ["| name | type | default | what it does |", "| --- | --- | --- | --- |"]
    out += [
        f"| `{r.name}`{' †' if r.pin else ''} | {r.type} | {r.default} | {r.doc} |" for r in rows
    ]
    pinned = [r for r in rows if r.pin]
    if pinned:
        out += [
            "",
            "† Also a session pin: a spawner writes it into the child, so a value you never",
            "set can be present. What it means there —",
            "",
            *[f"- `{r.name}` — {r.pin}" for r in pinned],
        ]
    return [*out, ""]


def render(budgets: list[Row], ours: list[Row], foreign: list[Row]) -> str:
    total = len(budgets) + len(ours) + len(foreign)
    out: list[str] = [
        "<!-- GENERATED by scripts/gen_env_reference.py — do not edit by hand. -->",
        "",
        "# Environment reference",
        "",
        "Every environment variable you may SET to configure ai-hats, with its type, its",
        "default and one line on what it does. Rendered from the declarations the code",
        f"itself reads by `scripts/gen_env_reference.py`, kept current by the `{CHECK}`",
        "stage of `scripts/ci-local.sh`.",
        "",
        "No value here is a copy. A budget's default is the very object its reader",
        "consults (`BUDGETS`, declared in the distribution that reads it), and a path's",
        "default is the resolution CHAIN its declaration spells, `x, else y`, whose last",
        "link is what a clean environment answers. The four budgets living in hooks that",
        "are copied into your project are the exception that proves the rule: those hooks",
        "cannot import `ai_hats`, so the generator reads the literal back out of the call",
        "site rather than letting the number have a second home.",
        "",
        "The gate proves this view matches those sources. It cannot prove the last",
        "column still describes what the code does — read type and default as derived,",
        "the sentence as a claim. It is equally silent about a knob the code reads that",
        "nobody declared: keeping the list COMPLETE is a separate guard's job, not this",
        "page's.",
        "",
        f"**{total} names: {len(budgets)} budgets, {len(ours)} paths of ours, "
        f"{len(foreign)} honoured from other tools.**",
        "",
        f"## Budgets ({len(budgets)})",
        "",
        "Numeric knobs. Unset, empty, unparsable, zero or negative all mean the default:",
        "a typo in a budget must never disarm the bound nor fail a session.",
        "",
        *_table(budgets),
        f"## Paths — ours ({len(ours)})",
        "",
        "Locations ai-hats resolves for itself. Each is an override: leave it unset and",
        "the chain in the default column runs, ending at the clean-environment answer.",
        "",
        *_table(ours),
        f"## Honoured from other tools ({len(foreign)})",
        "",
        "**Not ours.** We neither define these nor get to give them a default, so the",
        "default column stays empty — the only thing this project decides is where each",
        "ranks in a chain, which is what the last column says. Set them for the tool that",
        "owns them; ai-hats reads whatever it finds.",
        "",
        *_table(foreign),
        "## What is deliberately not on this page",
        "",
        "Three families of names are read by this code and still absent here, so this",
        f"page's {total} is not the whole vocabulary. **Bypass hatches** are recognised",
        "by the SHAPE of the name rather than by a table — see `withheld_from_subagent`",
        "in `src/ai_hats/constants.py` — because a table of them would fail open the way",
        "the roster beside it already did; the gate that refuses names the hatch that",
        "lifts it, which is where you meet one. **Spawn-envelope names** are written BY",
        "us into a child process rather than read from you, so a default is categorically",
        "meaningless for them; their contract is ADR-0025, and the hook-point half of it",
        "ADR-0020. The **rest** — install-time, diagnostics, gate command strings — have",
        "no home yet, and `git grep` is still the honest answer for those.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the page")
    mode.add_argument("--check", action="store_true", help="fail if the page is stale")
    ap.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="tree to read declarations from and write the page into (default: the repo)",
    )
    args = ap.parse_args(argv)

    root: Path = args.root
    page = root / PAGE_RELPATH
    try:
        budgets, ours, foreign = collect(root)
    except SourceError as exc:
        print(f"[{CHECK}] unreadable declaration: {exc}", file=sys.stderr)
        return 1

    rendered = render(budgets, ours, foreign)
    tally = f"{len(budgets) + len(ours) + len(foreign)} names"
    rel = page.relative_to(root) if page.is_relative_to(root) else page

    if args.write:
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(rendered, encoding="utf-8")
        print(f"[{CHECK}] wrote {rel} ({tally})")
        return 0

    current = page.read_text(encoding="utf-8") if page.exists() else None
    if current == rendered:
        print(f"[{CHECK}] current ({tally})")
        return 0
    where = "missing" if current is None else "stale"
    print(
        f"[{CHECK}] {rel} is {where} — run `python scripts/gen_env_reference.py --write`",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
