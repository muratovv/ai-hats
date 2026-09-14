#!/usr/bin/env python3
"""A tracker id must not ship in library prose, a living doc, or `src/`.

The library installs into other people's projects, where `PROJ-1430` is a dead
link. `.agent/` is gitignored, so the same holds for a fresh clone of this one,
and for a reader of `src/` who has the code but not the tracker.

Four things make this checkable rather than a matter of taste: the pattern needs
DIGITS, so CLI-grammar placeholders are not matches; a fenced block is a sample,
not a claim; a `TODO(<id>)` points FORWARD at work no commit holds yet, which is
the one id the comment rule blesses; and an id a machine PRINTS opts out by line
marker. The gate carries its own positive control — see `control_hits`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import _markdown

REPO_ROOT = Path(__file__).resolve().parent.parent

LIBRARY_RELPATH = "packages/ai-hats-library/src/ai_hats_library"

CHECK = "ticket-ids"

#: This repository's own prefix. Hardcoded on purpose: the gate lives in
#: `scripts/`, which ships to nobody, and no consumer has asked for a knob.
PREFIX = "HATS"

#: Digits are what separate an id from a placeholder: `HATS-NNN` teaches the
#: shape of an id and must survive, `HATS-1430` cites history and must not.
ID_RE = re.compile(rf"\b{PREFIX}-[0-9]+\b")

#: Composed so this file does not opt ITSELF out, and neither does prose that
#: merely documents the marker (the trick is `check_adr_integrity.py`'s).
OPT_OUT = f"{CHECK}: allow"
OPT_OUT_RE = re.compile(rf"{re.escape(OPT_OUT)}\b[ \t]*(?P<reason>[^\n>*#-]*)")

#: Prose, in the library. Its `.py` and `.sh` stay out: they install into other
#: people's trees, so a sweep there is a shipped-behaviour change rather than a
#: reference repair, and it has its own card.
SUFFIXES = (".md", ".yaml", ".yml")

#: This repository's own application code, swept and held at zero. `src/` is not
#: shipped as library content, so its comments and docstrings are read only by
#: someone standing in this repository — for whom `git log -S` resolves what an
#: id cannot. No suffix list: `src/` holds `.py` beside `.sh`, `.mjs` and two
#: extensionless hook scripts, and a corpus that named suffixes would have
#: missed the three of them that carried ids. Every file is read as text with
#: errors ignored, so a binary that appears there simply never matches.
CODE_RELPATH = "src"

#: The one id the comment rule blesses: it points FORWARD at work no commit
#: holds yet, and names the card that retires it. Masked out of a line before
#: the line is judged, so a `TODO(<id>)` sitting beside real provenance does not
#: shield it.
TODO_RE = re.compile(rf"TODO\({PREFIX}-[0-9]+[a-z]?\)")

#: `hooks` and `git_hooks` are distinct path segments: a single `hooks` glob
#: does not match the second.
EXCLUDED_SEGMENTS = frozenset({"hooks", "git_hooks"})

#: Where a tracker id is the record rather than a leak. These are also the
#: gate's positive control — see the module docstring.
CONTROL_PATHS = ("docs/adr", "CHANGELOG.md")

#: A dated record describes the tree of its own day. `check_prose_refs.py`
#: exempts exactly this set; a test pins the two in agreement rather than making
#: one gate import the other, so deleting either leaves the survivor working.
DATED_RECORD_RE = re.compile(r"^docs/(?:adr/|migration-v)")

#: The docs a human opens, beside the library. `README` and `CONTRIBUTING` are
#: named because they sit at the root, not under `docs/`.
ROOT_DOCS = ("README.md", "CONTRIBUTING.md")

UNCOVERED = (
    "`tests/`, `packages/` and `scripts/`: the same sweep, one tree at a time — "
    "judging a tree this gate has not swept would only report a backlog",
    "everything under `src/` IS judged, whatever its suffix — a corpus of `.py` "
    "alone would have missed the `.sh`, `.mjs` and extensionless hooks there",
    "library code — `.py`, `.sh`, `.go`, `.json` in the library: it installs into "
    "other people's trees, so a sweep there changes shipped behaviour",
    "`hooks/` and `git_hooks/`: same reason, and they are the highest-blast-"
    "radius surface in the repo",
    "`TODO(<id>)` anywhere: it names the card that retires it, which is the one "
    "thing `git log -S` cannot find, because the work is not in a commit yet",
    "`docs/adr/`, `docs/migration-v*.md` and `CHANGELOG.md`: the id there IS the "
    "record, and the first and last are this gate's positive control instead",
    "fenced code blocks: a sample teaches the shape of a command, so the ids in "
    "`rack transition HATS-042 --link depends_on:HATS-041` are templates rather "
    "than citations. An unclosed fence is reported instead of swallowed",
    "other trackers' ids: only this repository's TASK prefix is judged. Its "
    "hypothesis and proposal prefixes are the same dead link by the same "
    "argument, and `src/` still holds 16 of them — but two are code literals "
    "naming a catalog, so that sweep is a card and not a wider pattern here",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    token: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: `{self.token}`"


def corpus(root: Path) -> list[Path]:
    """Library prose, the living docs a reader opens, and this repo's `src/`."""
    seen: set[Path] = set()
    lib = root / LIBRARY_RELPATH
    if lib.is_dir():
        seen |= {
            path
            for path in lib.rglob("*")
            if path.suffix in SUFFIXES
            and path.is_file()
            and not EXCLUDED_SEGMENTS & set(path.relative_to(lib).parts)
        }
    seen |= living_docs(root)
    seen |= code(root)
    return sorted(seen)


def code(root: Path) -> set[Path]:
    """All of `src/` — comments and docstrings, judged as the prose they are."""
    src = root / CODE_RELPATH
    if not src.is_dir():
        return set()
    return {path for path in src.rglob("*") if path.is_file() and "__pycache__" not in path.parts}


def living_docs(root: Path) -> set[Path]:
    """`docs/**.md` minus the dated records, plus the two root files."""
    out = {root / name for name in ROOT_DOCS if (root / name).is_file()}
    docs = root / "docs"
    if docs.is_dir():
        out |= {
            path
            for path in docs.rglob("*.md")
            if not DATED_RECORD_RE.match(path.relative_to(root).as_posix())
        }
    return out


def allowed(line: str) -> str | None:
    """The reason this line's id is not provenance, or None if it claims none."""
    match = OPT_OUT_RE.search(line)
    if not match:
        return None
    return match.group("reason").strip() or "(no reason given)"


@dataclass(frozen=True)
class FileScan:
    findings: list[Finding]
    allowances: list[str]
    unclosed: int | None
    todos: int


def scan_file(path: Path, root: Path) -> FileScan:
    """Findings, allowances, an unclosed fence's line, and the TODOs spared."""
    findings: list[Finding] = []
    allowances: list[str] = []
    todos = 0
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix == ".md":
        lines, unclosed = _markdown.prose_lines(text)
    else:
        lines, unclosed = list(enumerate(text.splitlines(), start=1)), None
    for lineno, line in lines:
        todos += sum(len(ID_RE.findall(m.group(0))) for m in TODO_RE.finditer(line))
        masked = TODO_RE.sub("TODO(...)", line)
        tokens = ID_RE.findall(masked)
        if not tokens:
            continue
        reason = allowed(line)
        if reason is not None:
            allowances.append(f"{rel}:{lineno}: {', '.join(tokens)} — {reason}")
            continue
        findings.extend(Finding(rel, lineno, token) for token in tokens)
    return FileScan(findings, allowances, unclosed, todos)


def control_hits(root: Path) -> tuple[int, bool]:
    """Ids the same pattern still finds where history lives.

    Returns (count, corpus_present). An absent control corpus is not a failure —
    a scratch tree has no CHANGELOG — but a present one that yields zero is.
    """
    total = 0
    present = False
    for rel in CONTROL_PATHS:
        target = root / rel
        if not target.exists():
            continue
        present = True
        files = [target] if target.is_file() else list(target.rglob("*.md"))
        for path in files:
            total += len(ID_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return total, present


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    files = corpus(root)
    findings: list[Finding] = []
    allowances: list[str] = []
    unclosed: list[str] = []
    todos = 0
    for path in files:
        scan = scan_file(path, root)
        findings.extend(scan.findings)
        allowances.extend(scan.allowances)
        todos += scan.todos
        if scan.unclosed is not None:
            unclosed.append(f"{path.relative_to(root).as_posix()}:{scan.unclosed}")
    findings.sort(key=lambda f: (f.path, f.line))

    for finding in findings:
        print(f"[{CHECK}] FAIL: {finding}", file=sys.stderr)
    for allowance in allowances:
        print(f"[{CHECK}] allowed — {allowance}", file=sys.stderr)
    for where in sorted(unclosed):
        print(
            f"[{CHECK}] blind — {where} opens a code fence that never closes, so "
            f"everything after it is out of this gate's reach",
            file=sys.stderr,
        )

    control, control_present = control_hits(root)
    scope = f"{len(files)} files (library prose + living docs + src/)"

    if todos:
        plural = "site" if todos == 1 else "sites"
        print(
            f"[{CHECK}] spared: {todos} `TODO({PREFIX}-<id>)` {plural} — the one "
            f"form that points FORWARD, at work no commit holds yet",
            file=sys.stderr,
        )

    if control_present and control == 0:
        print(
            f"[{CHECK}] BROKEN: the pattern found no `{PREFIX}-<digits>` in "
            f"{' or '.join(CONTROL_PATHS)}, where the id is the record. "
            f"A clean {scope} proves nothing when the pattern itself is dead.",
            file=sys.stderr,
        )
        return 1

    if control_present:
        print(
            f"[{CHECK}] positive control: the pattern still finds {control} "
            f"{'id' if control == 1 else 'ids'} in "
            f"{' + '.join(CONTROL_PATHS)}",
            file=sys.stderr,
        )
    else:
        print(
            f"[{CHECK}] positive control: skipped, no {' or '.join(CONTROL_PATHS)}", file=sys.stderr
        )

    if findings:
        plural = "id" if len(findings) == 1 else "ids"
        print(f"[{CHECK}] {len(findings)} tracker {plural} across {scope}", file=sys.stderr)
        print(
            f"[{CHECK}] drop the id and keep the WHY in one line; an id a machine "
            f"PRINTS keeps its place with `{OPT_OUT} <why>` on the same line",
            file=sys.stderr,
        )
    else:
        print(f"[{CHECK}] ok: no tracker id in {scope}", file=sys.stderr)
    for item in UNCOVERED:
        print(f"[{CHECK}] not covered: {item}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
