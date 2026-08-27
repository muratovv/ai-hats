#!/usr/bin/env python3
"""A tracker id must not ship inside library prose.

The library installs into other people's projects, where `PROJ-1430` is a dead
link and `git log -S` is the road that actually travels.

Two things make this checkable rather than a matter of taste: the pattern needs
DIGITS, so CLI-grammar placeholders are not matches at all; and an id a machine
PRINTS opts out by line marker, because that prose names the screen, not history.

The gate carries its own positive control — see `control_hits`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

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

UNCOVERED = (
    "library code — `.py`, `.sh`, `.go`, `.json` in the library: a bare id with "
    "no words has to be REWRITTEN, not deleted, so it is a separate card",
    "`hooks/` and `git_hooks/`: same reason, and they are the highest-blast-"
    "radius surface in the repo",
    "`docs/adr/` and `CHANGELOG.md`: the id there IS the record — they are this "
    "gate's positive control instead",
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
    lib = root / LIBRARY_RELPATH
    if not lib.is_dir():
        return []
    out = [
        path
        for path in lib.rglob("*")
        if path.suffix in SUFFIXES
        and path.is_file()
        and not EXCLUDED_SEGMENTS & set(path.relative_to(lib).parts)
    ]
    return sorted(out)


def allowed(line: str) -> str | None:
    """The reason this line's id is not provenance, or None if it claims none."""
    match = OPT_OUT_RE.search(line)
    if not match:
        return None
    return match.group("reason").strip() or "(no reason given)"


def scan_file(path: Path, root: Path) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    allowances: list[str] = []
    rel = path.relative_to(root).as_posix()
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
    ):
        tokens = ID_RE.findall(line)
        if not tokens:
            continue
        reason = allowed(line)
        if reason is not None:
            allowances.append(f"{rel}:{lineno}: {', '.join(tokens)} — {reason}")
            continue
        findings.extend(Finding(rel, lineno, token) for token in tokens)
    return findings, allowances


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
    for path in files:
        file_findings, file_allowances = scan_file(path, root)
        findings.extend(file_findings)
        allowances.extend(file_allowances)
    findings.sort(key=lambda f: (f.path, f.line))

    for finding in findings:
        print(f"[{CHECK}] FAIL: {finding}", file=sys.stderr)
    for allowance in allowances:
        print(f"[{CHECK}] allowed — {allowance}", file=sys.stderr)

    control, control_present = control_hits(root)
    scope = f"{len(files)} library prose files"

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
