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
is "this view is current", never "these rows are true". Check B resolves an
`ai-hats` line's options too, but for `rack` and `python -m` forms the
subcommand path only — there `rack ls --nonesuch` passes although `ls` declares
no such option. Check A accepts an id found
anywhere in the file's git log, including the commit that wrote the flow block
itself, so the cataloguing card's own id grounds a pin trivially (HATS-1567).

comment-length: allow
"""

from __future__ import annotations

import argparse
import ast
import functools
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import click

REPO_ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = REPO_ROOT / "tests" / "e2e"
CATALOG = E2E_DIR / "CATALOG.md"

FIELDS = ("flow", "cmds", "expect", "why")
_HEADER = re.compile(r"^e2e\s*\(([^)]*)\)\s*$")
_FIELD = re.compile(rf"^({'|'.join(FIELDS)}):\s?(.*)$")
_ID = re.compile(r"HATS-\d+")
_NON_HUMAN_ACTOR = re.compile(
    r"^(a test suite|a test runner|a test harness|pytest|ci)\b", re.IGNORECASE
)


def check_actor(rows: list[Row]) -> list[str]:
    errors = []
    for row in rows:
        match = _NON_HUMAN_ACTOR.match(row.flow.strip())
        if match:
            actor = match.group(1)
            errors.append(
                f"{row.file}: flow opens with non-human actor {actor!r} — flow must describe what a person does"
            )
    return errors


def check_pins(rows: list[Row], ids_known_for: Callable[[str], set[str]]) -> list[str]:
    errors = []
    for row in rows:
        known = ids_known_for(row.file)
        for pin in row.pins:
            if pin not in known:
                errors.append(
                    f"{row.file}: header pin {pin} has no basis (absent from file text and git log)"
                )
    return errors


def check_plumbing(rows: list[Row]) -> list[str]:
    errors = []
    for row in rows:
        for ln in row.cmds:
            ln_s = ln.strip()
            if not ln_s or ln_s.startswith("#"):
                continue
            code = ln_s.split("#")[0].strip()
            if not code:
                continue
            tokens = code.split()
            leading = tokens[0] if tokens else ""
            is_plumbing = False
            if leading == "pytest":
                is_plumbing = True
            elif leading == "python" and len(tokens) >= 3 and tokens[1] == "-m":
                mod_name = tokens[2]
                if mod_name.startswith("_helpers") or mod_name.startswith("ai_hats._"):
                    is_plumbing = True
            elif leading in ("_helpers", "conftest"):
                is_plumbing = True

            if is_plumbing:
                errors.append(
                    f"{row.file}: cmds `{ln_s}` demonstrates test plumbing — show the user-facing command instead"
                )
    return errors


DYNAMIC_RACK_GROUPS = {"hyp", "proposal", "prop"}


def _resolve_cli_cmd(
    group: click.Group,
    sub_args: list[str],
    cli_name: str = "",
    is_top_level: bool = True,
) -> tuple[bool, str | None]:
    if not sub_args:
        return True, None

    ctx = click.Context(group, resilient_parsing=True)
    parser = group.make_parser(ctx)
    try:
        opts, args_rem, _ = parser.parse_args(args=list(sub_args))
    except Exception as exc:
        return False, f"invalid options for {group.name}: {exc}"

    if not args_rem:
        return True, None

    subcmd = args_rem[0]
    if isinstance(group, click.Group):
        if subcmd in group.commands:
            child_cmd = group.commands[subcmd]
            if isinstance(child_cmd, click.Group):
                return _resolve_cli_cmd(child_cmd, args_rem[1:], cli_name, is_top_level=False)
            else:
                return True, None
        elif cli_name == "ai-hats" and is_top_level:
            if len(sub_args) == 1 and subcmd.isalnum():
                cmds_avail = ", ".join(sorted(group.commands.keys()))
                return (
                    False,
                    f"unknown subcommand {subcmd!r} under {group.name} (available: {cmds_avail})",
                )
            return True, None
        elif cli_name == "rack" and subcmd in DYNAMIC_RACK_GROUPS:
            return True, None
        else:
            cmds_avail = ", ".join(sorted(group.commands.keys()))
            return (
                False,
                f"unknown subcommand {subcmd!r} under {group.name} (available: {cmds_avail})",
            )
    return True, None


def check_cmds(
    rows: list[Row],
    resolve_cmd: Callable[[str, list[str]], tuple[bool, str | None]],
    path_exists: Callable[[str], bool],
) -> list[str]:
    errors = []
    for row in rows:
        for raw_ln in row.cmds:
            ln = raw_ln.strip()
            if not ln or ln.startswith("#"):
                continue

            if ".../" in ln:
                errors.append(f"{row.file}: cmds `{ln}` uses literal `.../` ellipsis")
                continue

            marker_match = re.search(r"#\s*no-resolve:(.*)", ln)
            if marker_match:
                reason = marker_match.group(1).strip()
                if not reason:
                    errors.append(
                        f"{row.file}: cmds `{ln}` has empty excuse reason after `# no-resolve:`"
                    )
                continue

            code = ln.split("#")[0].strip()
            if not code:
                continue

            try:
                tokens = shlex.split(code)
            except Exception:
                tokens = code.split()

            if not tokens:
                continue

            cmd_head = tokens[0]
            # A bare `ai-hats` head belongs to validate_cmd_line, which resolves
            # options too; resolving it here as well reported one defect twice,
            # in two wordings, once both checks ran in the same phase (HATS-1644).
            if cmd_head == "rack" or (
                cmd_head == "python"
                and len(tokens) >= 3
                and tokens[1] == "-m"
                and tokens[2] in ("ai_hats", "ai_hats.cli", "ai_hats_rack")
            ):
                if cmd_head == "rack":
                    cli_name = cmd_head
                    sub_args = tokens[1:]
                else:
                    cli_name = "ai-hats" if tokens[2].startswith("ai_hats") else "rack"
                    sub_args = tokens[3:]

                sub_args_clean = [a for a in sub_args if a not in ("--version", "--help", "-h")]
                if sub_args_clean:
                    ok, err = resolve_cmd(cli_name, sub_args_clean)
                    if not ok:
                        errors.append(f"{row.file}: cmds `{ln}` — {err}")

            for token in tokens:
                if (
                    token.startswith("-")
                    or token.startswith("http://")
                    or token.startswith("https://")
                ):
                    continue
                if token.startswith("<") and token.endswith(">"):
                    continue
                if token.startswith(
                    ("scripts/", "tests/", "docs/", "src/", "packages/", ".agent/", ".github/")
                ) or token in ("pyproject.toml", "README.md", "CONTRIBUTING.md"):
                    if not path_exists(token):
                        errors.append(f"{row.file}: cmds `{ln}` references missing path `{token}`")
    return errors


def _get_cli_trees() -> tuple[click.Group, click.Group]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from ai_hats.cli import main as ai_hats_cli
    except ImportError as exc:
        raise RuntimeError(f"[e2e-catalog] cannot import ai_hats.cli: {exc}") from exc

    try:
        from ai_hats_rack.cli import main as rack_cli
    except ImportError as exc:
        raise RuntimeError(f"[e2e-catalog] cannot import ai_hats_rack.cli: {exc}") from exc

    return ai_hats_cli, rack_cli


@functools.lru_cache(maxsize=None)
def _ids_known_for(file_name: str, base_dir: Path = E2E_DIR) -> set[str]:
    file_path = base_dir / file_name
    found = set()
    if file_path.exists():
        text = file_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
            doc = ast.get_docstring(tree, clean=False)
            if doc and doc in text:
                text = text.replace(doc, "", 1)
        except Exception:  # noqa: S110 # silent-ok: docstring parsing is best-effort
            pass
        found.update(_ID.findall(text))
    try:
        # In the tree that OWNS the file, not this checkout: grounding a pin in
        # a repository nobody named is a basis the guard never checked (HATS-1644).
        res = subprocess.run(
            ["git", "log", "--", file_name],
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            found.update(_ID.findall(res.stdout))
    except Exception:  # noqa: S110 # silent-ok: git log lookup is optional
        pass
    return found


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


def validate_cmd_line(cmd_str: str) -> str | None:
    """Verify that a `cmds:` line starting with `ai-hats` is valid Click CLI syntax.

    Returns an error message if invalid, or None if valid/ignored.
    Lines containing `# retired:` are ignored as declared opt-outs.
    """
    if "# retired:" in cmd_str or "# no-resolve:" in cmd_str:
        return None

    clean = cmd_str.split("#")[0].strip()
    if not clean:
        return None

    if not (clean == "ai-hats" or clean.startswith("ai-hats ") or clean.startswith("ai-hats\t")):
        return None

    try:
        tokens = shlex.split(clean)
    except Exception as exc:
        return f"cannot parse command line: {exc}"

    args = tokens[1:]  # skip 'ai-hats'
    from ai_hats.cli import main

    curr_cmd: click.Command = main
    ctx = click.Context(main, info_name="ai-hats")

    i = 0
    saw_dash_dash = False

    while i < len(args):
        arg = args[i]

        if arg == "--":
            saw_dash_dash = True
            i += 1
            continue

        if not saw_dash_dash and arg.startswith("-"):
            if arg in ("--help", "-h", "--version"):
                i += 1
                continue

            param = next(
                (p for p in curr_cmd.params if arg in p.opts or arg in p.secondary_opts),
                None,
            )
            if param is None:
                return f"unknown option {arg!r} for command {ctx.info_name!r}"

            if param.is_flag or param.nargs == 0:
                i += 1
            else:
                n = max(1, param.nargs) if param.nargs != -1 else 1
                i += 1 + n
            continue

        if isinstance(curr_cmd, click.Group):
            sub_cmd = curr_cmd.get_command(ctx, arg)
            if sub_cmd is not None:
                curr_cmd = sub_cmd
                ctx = click.Context(curr_cmd, parent=ctx, info_name=arg)
                i += 1
                continue
            if curr_cmd is main and getattr(curr_cmd, "allow_extra_args", False):
                if " " in arg or (i > 0 and not arg.isalnum()):
                    i += 1
                    continue
            return f"unknown subcommand {arg!r} for command {ctx.info_name!r}"

        args_params = [p for p in curr_cmd.params if isinstance(p, click.Argument)]
        if args_params:
            i += 1
            continue
        return f"unexpected argument {arg!r} for command {ctx.info_name!r}"

    return None


def check_cmd_lines(rows: list[Row]) -> list[str]:
    """Rows whose `cmds:` do not resolve against the real CLI trees.

    A row, not a block: the block parsed fine, so reporting this as malformed
    sends the reader to the docstring's shape when the defect is in what it says
    (HATS-1644).
    """
    errors = []
    for row in rows:
        for cmd in row.cmds:
            err = validate_cmd_line(cmd)
            if err:
                errors.append(f"{row.file}: invalid `cmds:` line {cmd!r} — {err}")
    return errors


def collect(e2e_dir: Path) -> tuple[list[Row], list[str], list[str]]:
    """(rows, uncatalogued file names, errors) over every test file in `e2e_dir`.

    ``errors`` is malformed blocks only — a block that cannot be read as a row.
    Whether a readable row is SOUND is judged later, by the check_* family.
    """
    rows: list[Row] = []
    pending: list[str] = []
    errors: list[str] = []
    for path in sorted(e2e_dir.glob("test_*.py")):
        try:
            found = parse_rows(path.read_text(encoding="utf-8"), path.name)
        except CatalogError as exc:
            errors.append(str(exc))
            continue
        if found:
            rows.extend(found)
        else:
            pending.append(path.name)
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
    ap.add_argument(
        "--dir",
        type=Path,
        default=E2E_DIR,
        help="path to e2e directory (default: tests/e2e)",
    )
    args = ap.parse_args(argv)

    e2e_dir = args.dir
    catalog_file = e2e_dir / "CATALOG.md"

    rows, pending, errors = collect(e2e_dir)
    if errors:
        print("[e2e-catalog] malformed flow block(s):", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    ack = os.environ.get("AI_HATS_E2E_CATALOG_ACK") == "1"

    if pending:
        if ack:
            print(
                f"[e2e-catalog] BYPASSED via AI_HATS_E2E_CATALOG_ACK=1 "
                f"(uncatalogued files allowed: {', '.join(pending)})",
                file=sys.stderr,
            )
        else:
            print(
                f"[e2e-catalog] refusal — {len(pending)} uncatalogued file(s):",
                file=sys.stderr,
            )
            for name in pending:
                print(f"  - {name}", file=sys.stderr)
            print(
                "  Remedy: write the four-field block (`flow:`, `cmds:`, `expect:`, `why:`)\n"
                "  in the module docstring (schema in `scripts/gen_e2e_catalog.py`;\n"
                "  reference: `tests/e2e/test_agy_bypass.py`).",
                file=sys.stderr,
            )
            if args.write:
                rendered = render(rows, pending)
                catalog_file.write_text(rendered, encoding="utf-8")
                done = len({row.file for row in rows})
                tally = f"{done}/{done + len(pending)} files, {len(rows)} flows"
                rel = (
                    catalog_file.relative_to(REPO_ROOT)
                    if catalog_file.is_relative_to(REPO_ROOT)
                    else catalog_file
                )
                print(f"[e2e-catalog] wrote {rel} ({tally})")
            return 1

    ai_hats_cli, rack_cli = _get_cli_trees()

    def resolve_cmd(cli_name: str, sub_args: list[str]) -> tuple[bool, str | None]:
        root_group = ai_hats_cli if cli_name == "ai-hats" else rack_cli
        return _resolve_cli_cmd(root_group, sub_args, cli_name)

    def path_exists(rel_path: str) -> bool:
        return (REPO_ROOT / rel_path).exists()

    soundness_errors = (
        check_actor(rows)
        + check_pins(rows, lambda f: _ids_known_for(f, e2e_dir))
        + check_plumbing(rows)
        + check_cmds(rows, resolve_cmd, path_exists)
        + check_cmd_lines(rows)
    )
    if soundness_errors:
        print("[e2e-catalog] unsound row(s):", file=sys.stderr)
        for err in soundness_errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    rendered = render(rows, pending)
    # Files, not rows — a multi-flow file is still one file (it would read as
    # more progress than there is).
    done = len({row.file for row in rows})
    tally = f"{done}/{done + len(pending)} files, {len(rows)} flows"
    rel = (
        catalog_file.relative_to(REPO_ROOT)
        if catalog_file.is_relative_to(REPO_ROOT)
        else catalog_file
    )

    if args.write:
        catalog_file.write_text(rendered, encoding="utf-8")
        print(f"[e2e-catalog] wrote {rel} ({tally})")
        return 0

    current = catalog_file.read_text(encoding="utf-8") if catalog_file.exists() else None
    if current == rendered:
        print(f"[e2e-catalog] current ({tally})")
        return 0
    where = "missing" if current is None else "stale"
    print(
        f"[e2e-catalog] {rel} is {where} — run `python scripts/gen_e2e_catalog.py --write`",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
