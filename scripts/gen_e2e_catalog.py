#!/usr/bin/env python3
"""HATS-1498 — render tests/e2e/CATALOG.md from the flow block in each test.

No CI job runs the e2e tier; its only consumer is the maintainer pre-push gate.
To be reviewable it has to read as a list of user flows, so each test's module
docstring carries a structured block:

    \"\"\"e2e (HATS-788)

    flow:   what the user is doing, in one sentence
    cmds:
        the commands they would actually type
    expect: the observable outcome
    why:    what breaks without this guard
    \"\"\"

The docstring is the source of truth (it cannot drift from the file it sits
in); this script renders the whole-tier view that answers "is this flow already
covered?", and `--check` keeps that view current. What `--check` CANNOT catch
is a docstring drifting from its own code — both go stale together. The claim
is "this view is current", never "these rows are true".

comment-length: allow
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = REPO_ROOT / "tests" / "e2e"
CATALOG = E2E_DIR / "CATALOG.md"

FIELDS = ("flow", "cmds", "expect", "why")
_HEADER = re.compile(r"^e2e\s*\(([^)]*)\)\s*$")
_FIELD = re.compile(rf"^({'|'.join(FIELDS)}):\s?(.*)$")
_ID = re.compile(r"HATS-\d+")


class CatalogError(Exception):
    """A block that exists but cannot be read as a row."""


@dataclass(frozen=True)
class Row:
    file: str
    pins: list[str]
    flow: str
    cmds: list[str]
    expect: str
    why: str


def _dedent(lines: list[str]) -> list[str]:
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    cut = min(indents) if indents else 0
    return [ln[cut:] if ln.strip() else "" for ln in lines]


def parse_rows(src: str, name: str) -> list[Row]:
    """Every row in `src`'s module docstring; empty when it carries no block.

    A file may pin more than one flow — `tests/e2e/test_prepush_e2e_master_gate.py`
    holds a `git push` check mode and a `run-e2e-gate.sh` run mode — so each
    `flow:` at column 0 opens a new row, all sharing the file's header ids.

    Raises CatalogError when a block is present but incomplete: a half-written
    block must be named, never dropped into the uncatalogued bucket where it
    would read as "not done yet" forever.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        raise CatalogError(f"{name}: cannot parse: {exc}") from exc
    doc = ast.get_docstring(tree, clean=False)
    if doc is None:
        return []

    lines = doc.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("flow:")]
    if not starts:
        if any(_FIELD.match(ln) for ln in lines):
            raise CatalogError(f"{name}: block has fields but no `flow:` to open a row")
        return []

    header = next((ln for ln in lines if ln.strip()), "")
    match = _HEADER.match(header.strip())
    if match is None:
        raise CatalogError(
            f"{name}: first docstring line must be `e2e (HATS-NNN[, HATS-NNN...])`, got {header!r}"
        )
    pins = _ID.findall(match.group(1))
    if not pins:
        raise CatalogError(f"{name}: header names no HATS id — the row loses its provenance")

    bounds = [*starts, len(lines)]
    return [_parse_block(lines[bounds[i] : bounds[i + 1]], name, pins) for i in range(len(starts))]


def _parse_block(lines: list[str], name: str, pins: list[str]) -> Row:
    collected: dict[str, list[str]] = {}
    current: str | None = None
    for raw in lines:
        field = _FIELD.match(raw)
        if field:
            current = field.group(1)
            if current in collected:
                raise CatalogError(f"{name}: field {current!r} appears twice")
            collected[current] = [field.group(2)] if field.group(2).strip() else []
        elif current is not None and raw.strip():
            # Unindented prose would be absorbed into the last field silently.
            if not raw[:1].isspace():
                raise CatalogError(
                    f"{name}: unindented prose after the block ({raw.strip()[:40]!r}) — "
                    "the block must be the whole docstring; indent it to continue a field"
                )
            collected[current].append(raw)

    missing = [f for f in FIELDS if f not in collected]
    if missing:
        raise CatalogError(f"{name}: block is missing {', '.join(missing)}")
    empty = [f for f in FIELDS if not collected[f]]
    if empty:
        raise CatalogError(f"{name}: field(s) {', '.join(empty)} are empty")

    def prose(field: str) -> str:
        return " ".join(ln.strip() for ln in collected[field] if ln.strip())

    return Row(
        file=name,
        pins=pins,
        flow=prose("flow"),
        cmds=_dedent(collected["cmds"]),
        expect=prose("expect"),
        why=prose("why"),
    )


def collect(e2e_dir: Path) -> tuple[list[Row], list[str], list[str]]:
    """(rows, uncatalogued file names, errors) over every test file in `e2e_dir`."""
    rows: list[Row] = []
    pending: list[str] = []
    errors: list[str] = []
    for path in sorted(e2e_dir.glob("test_*.py")):
        try:
            found = parse_rows(path.read_text(encoding="utf-8"), path.name)
        except CatalogError as exc:
            errors.append(str(exc))
            continue
        (rows.extend(found) if found else pending.append(path.name))
    return rows, pending, errors


def render(rows: list[Row], pending: list[str]) -> str:
    files = list(dict.fromkeys(row.file for row in rows))
    total = len(files) + len(pending)
    out: list[str] = [
        "<!-- GENERATED by scripts/gen_e2e_catalog.py — do not edit by hand. -->",
        "",
        "# e2e flow catalog",
        "",
        "Every test in `tests/e2e/` as the user flow it pins, in the commands a",
        "user would actually type. Source of truth is the structured block in each",
        "test's module docstring; this file is rendered from it by",
        "`scripts/gen_e2e_catalog.py` and kept current by the `e2e-catalog` stage of",
        "`scripts/ci-local.sh`.",
        "",
        "That gate proves this view matches the docstrings. It cannot prove a",
        "docstring still matches its own test — both go stale together. Treat a row",
        "as a claim to check, not as evidence.",
        "",
        f"**{len(files)} of {total} files catalogued — "
        f"{len(rows)} flow{'' if len(rows) == 1 else 's'}.**",
        "",
    ]
    for name in files:
        here = [row for row in rows if row.file == name]
        out += [f"## `{name}`", "", f"*pins {', '.join(here[0].pins)}*", ""]
        for row in here:
            out += [
                f"- **flow** — {row.flow}",
                "- **cmds**",
                "",
                "  ```console",
                *[f"  {ln}".rstrip() for ln in row.cmds],
                "  ```",
                "",
                f"- **expect** — {row.expect}",
                f"- **why** — {row.why}",
                "",
            ]
    if pending:
        out += [
            "## Not yet catalogued",
            "",
            f"{len(pending)} files carry no flow block yet:",
            "",
            *[f"- `{name}`" for name in pending],
            "",
        ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the catalog")
    mode.add_argument("--check", action="store_true", help="fail if the catalog is stale")
    args = ap.parse_args(argv)

    rows, pending, errors = collect(E2E_DIR)
    if errors:
        print("[e2e-catalog] malformed flow block(s):", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    rendered = render(rows, pending)
    # Files, not rows — a multi-flow file is still one file (it would read as
    # more progress than there is).
    done = len({row.file for row in rows})
    tally = f"{done}/{done + len(pending)} files, {len(rows)} flows"
    if args.write:
        CATALOG.write_text(rendered, encoding="utf-8")
        print(f"[e2e-catalog] wrote {CATALOG.relative_to(REPO_ROOT)} ({tally})")
        return 0

    current = CATALOG.read_text(encoding="utf-8") if CATALOG.exists() else None
    if current == rendered:
        print(f"[e2e-catalog] current ({tally})")
        return 0
    where = "missing" if current is None else "stale"
    print(
        f"[e2e-catalog] {CATALOG.relative_to(REPO_ROOT)} is {where} — "
        "run `python scripts/gen_e2e_catalog.py --write`",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
