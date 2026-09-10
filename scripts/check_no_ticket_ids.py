#!/usr/bin/env python3
"""A tracker id must not ship inside library prose, nor sit in a living doc.

The library installs into other people's projects, where `PROJ-1430` is a dead
link. `.agent/` is gitignored, so the same holds for a fresh clone of this one.

Three things make this checkable rather than a matter of taste: the pattern
needs DIGITS, so CLI-grammar placeholders are not matches; a fenced block is a
sample, not a claim; and an id a machine PRINTS opts out by line marker. The
gate carries its own positive control — see `control_hits`.
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

#: Prose only. Library code carries ids too, but removing one there is a
#: rewrite rather than a deletion — a bare `# PROJ-1242` with no words loses
#: all its content when the number goes. That is a separate card.
SUFFIXES = (".md", ".yaml", ".yml")

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
    "library code — `.py`, `.sh`, `.go`, `.json` in the library: a bare id with "
    "no words has to be REWRITTEN, not deleted, so it is a separate card",
    "`hooks/` and `git_hooks/`: same reason, and they are the highest-blast-"
    "radius surface in the repo",
    "`docs/adr/`, `docs/migration-v*.md` and `CHANGELOG.md`: the id there IS the "
    "record, and the first and last are this gate's positive control instead",
    "fenced code blocks: a sample teaches the shape of a command, so the ids in "
    "`rack transition HATS-042 --link depends_on:HATS-041` are templates rather "
    "than citations. An unclosed fence is reported instead of swallowed",
    "other trackers' ids: only this repository's prefix is judged",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    token: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: `{self.token}`"


def corpus(root: Path) -> list[Path]:
    """Library prose, plus the living docs a reader of this repository opens."""
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
    return sorted(seen)


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


def scan_file(path: Path, root: Path) -> tuple[list[Finding], list[str], int | None]:
    """Findings, allowances, and the line of an unclosed fence if there is one."""
    findings: list[Finding] = []
    allowances: list[str] = []
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix == ".md":
        lines, unclosed = _markdown.prose_lines(text)
    else:
        lines, unclosed = list(enumerate(text.splitlines(), start=1)), None
    for lineno, line in lines:
        tokens = ID_RE.findall(line)
        if not tokens:
            continue
        reason = allowed(line)
        if reason is not None:
            allowances.append(f"{rel}:{lineno}: {', '.join(tokens)} — {reason}")
            continue
        findings.extend(Finding(rel, lineno, token) for token in tokens)
    return findings, allowances, unclosed


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
    for path in files:
        file_findings, file_allowances, opened_at = scan_file(path, root)
        findings.extend(file_findings)
        allowances.extend(file_allowances)
        if opened_at is not None:
            unclosed.append(f"{path.relative_to(root).as_posix()}:{opened_at}")
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
    scope = f"{len(files)} prose files (library + living docs)"

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
