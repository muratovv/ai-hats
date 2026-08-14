#!/usr/bin/env python3
"""A citation into a decision record must resolve, and a number must name one file.

HATS-1646. Prose carries no assert, so a rotted citation stays green for months —
and an ambiguous number had already corrupted one automated inference.

The non-obvious half is what "declared" means: opening a line, or being bold.
Matching headers alone flags the corpus's table and list markers; matching
anywhere in the text goes green on a real dangling one (`D5` occurs in ADR-0020
only as a citation of *other* ADRs). What this misses is printed on every run.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = ("docs", "src", "packages", "tests", "scripts")
ROOT_GLOB = "*.md"  # README / CONTRIBUTING / CHANGELOG cite ADRs too
SCAN_SUFFIXES = frozenset({".md", ".py", ".sh", ".yaml", ".yml"})
EXCLUDED_PARTS = frozenset({".git", "__pycache__", ".venv", "node_modules", "site-packages"})

ADR_DIR = "docs/adr"

CHECK = "adr-integrity"

# Opt-out for a file whose citations are test data. Composed, so the sentinel never
# appears whole here — spelled out, the checker opts ITSELF out (HATS-1646).
OPT_OUT = f"{CHECK}: fixtures"

# It must OPEN a line, exactly as a decision marker must: prose that merely mentions
# the sentinel (CONTRIBUTING documents it) is a mention, not a declaration.
OPT_OUT_RE = re.compile(rf"^[ \t]*(?:#|//|<!--)?[ \t]*{re.escape(OPT_OUT)}", re.MULTILINE)

# Marker = uppercase letter + number, optional letter suffix (`D4a`), optional
# sub-index (`P0 #4`), optional leading `§`. Prose names never match — see UNCOVERED.
REF = re.compile(r"ADR-(\d{4})(?:\s+§?([A-Z]\d{1,2}[a-z]?(?:\s*#\s*\d+)?))?")

UNCOVERED = (
    "references from .agent/ (tracker cards are transient state, not the corpus)",
    "prose section names — `ADR-0014 Composition rule`, `ADR-0009 safe direction`",
    "numeric sections — `ADR-0017 §4`, `ADR-0001 §1-§3`: the citation writes `§2` where "
    "the declaration reads `## 2.`, so the anchor would degrade to a bare digit",
    "markdown anchors and external URLs — a different corpus, a different tool",
    "bare `ADR-NNNN` citations carry no marker, so only the number is checked",
)


@dataclass(frozen=True)
class DanglingRef:
    path: str  # repo-relative
    line: int
    number: str
    marker: str  # "" when the number itself is unknown

    def __str__(self) -> str:
        if not self.marker:
            return (
                f"{self.path}:{self.line}: cites ADR-{self.number}, and no file in "
                f"{ADR_DIR}/ carries that number."
            )
        return (
            f"{self.path}:{self.line}: cites ADR-{self.number} {self.marker}, which "
            f"ADR-{self.number} does not declare. A marker is declared where it opens a "
            f"line (header, table row, list item, optionally inside a blockquote) or "
            f"where it is **bold**. Cite a marker that exists, or declare this one."
        )


@dataclass(frozen=True)
class DuplicateNumber:
    number: str
    files: tuple[str, ...]

    def __str__(self) -> str:
        return (
            f"ADR-{self.number} names {len(self.files)} files ({', '.join(self.files)}). "
            f"A citation of a marker cannot resolve to one of them, so the number must "
            f"name exactly one — renumber the later record and move its citations."
        )


def adr_index(root: Path) -> dict[str, list[Path]]:
    """ADR number -> the file(s) claiming it. More than one is the defect."""
    index: dict[str, list[Path]] = {}
    for path in sorted((root / ADR_DIR).glob("[0-9][0-9][0-9][0-9]-*.md")):
        index.setdefault(path.name[:4], []).append(path)
    return index


def declares(text: str, marker: str) -> bool:
    """Is `marker` declared in `text` — by position, or by typography?

    Position (opening a line) and bold are both declaration acts; a plain
    mid-sentence mention is not, which is how ADR-0020's missing D5 stays caught.
    Bold has to count wherever it falls: ADR-0014 lists `**T2**`…`**T15**` as one
    wrapped paragraph, so which of them opens a line is an artifact of wrapping.
    """
    parts = [p for p in re.split(r"\s+", marker.replace("#", " #")) if p]
    body = r"\s*".join(re.escape(p) for p in parts)
    boundary = r"(?=[\s.:,—–)|-]|$)"  # so `D1` never matches `D10`
    opens_line = (
        r"^[ \t]*(?:>[ \t]*)*"  # optionally inside a blockquote
        r"(?:\#{1,4}[ \t]+|\|[ \t]*|[-*][ \t]+)?"  # header | table row | list item
        rf"\*{{0,2}}{body}\*{{0,2}}{boundary}"
    )
    bolded = rf"\*\*{body}\*\*{boundary}"
    return any(re.search(p, text, re.MULTILINE) for p in (opens_line, bolded))


def scan_files(root: Path) -> list[Path]:
    seen: set[Path] = set(root.glob(ROOT_GLOB))
    for name in SCAN_DIRS:
        for path in (root / name).rglob("*"):
            if path.is_file() and path.suffix in SCAN_SUFFIXES:
                seen.add(path)
    return sorted(p for p in seen if not EXCLUDED_PARTS & set(p.parts))


def references(text: str) -> list[tuple[int, str, str]]:
    """(line, number, marker) for every ADR citation; marker is "" when bare."""
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in REF.finditer(line):
            number, marker = match.group(1), match.group(2) or ""
            found.append((lineno, number, re.sub(r"\s+", " ", marker).strip()))
    return found


def scan(root: Path) -> tuple[list[DanglingRef], list[DuplicateNumber], list[str]]:
    index = adr_index(root)
    bodies = {num: [p.read_text(encoding="utf-8") for p in paths] for num, paths in index.items()}

    duplicates = [
        DuplicateNumber(num, tuple(p.name for p in paths))
        for num, paths in sorted(index.items())
        if len(paths) > 1
    ]

    dangling: list[DanglingRef] = []
    opted_out: list[str] = []
    for path in scan_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"[adr-integrity] skipped unreadable {path}: {exc}", file=sys.stderr)
            continue
        rel = path.relative_to(root).as_posix()
        if OPT_OUT_RE.search(text):
            opted_out.append(rel)
            continue
        for lineno, number, marker in references(text):
            if number not in index:
                dangling.append(DanglingRef(rel, lineno, number, ""))
            elif marker and not any(declares(body, marker) for body in bodies[number]):
                dangling.append(DanglingRef(rel, lineno, number, marker))
    return sorted(dangling, key=lambda d: (d.path, d.line)), duplicates, sorted(opted_out)


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    dangling, duplicates, opted_out = scan(root)

    for duplicate in duplicates:
        print(f"[adr-integrity] FAIL: {duplicate}", file=sys.stderr)
    for ref in dangling:
        print(f"[adr-integrity] FAIL: {ref}", file=sys.stderr)
    for path in opted_out:
        print(f"[adr-integrity] fixtures, not prose — skipped {path}", file=sys.stderr)

    files = scan_files(root)
    marker_refs = sum(
        1
        for p in files
        for _, _, m in references(p.read_text(encoding="utf-8", errors="ignore"))
        if m
    )
    scope = f"{len(files)} files in {', '.join(SCAN_DIRS)} + root {ROOT_GLOB}"
    if dangling or duplicates:
        print(f"[adr-integrity] scanned {scope}; {marker_refs} marker citations", file=sys.stderr)
    else:
        print(
            f"[adr-integrity] ok: {marker_refs} marker citations resolve across {scope}; "
            f"every ADR number names one file",
            file=sys.stderr,
        )
    for item in UNCOVERED:
        print(f"[adr-integrity] not covered: {item}", file=sys.stderr)
    return 1 if (dangling or duplicates) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
