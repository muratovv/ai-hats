#!/usr/bin/env python3
"""A reference in library prose must resolve, the way an ADR citation must.

HATS-1825. The generalisation of `check_adr_integrity.py` is NOT "check every
backticked token" — that reads 61% of an ordinary corpus as a defect. It is a
small set of reference SHAPES, each exact enough that a finding is a fact.

The non-obvious half is the anchor: a path is a claim about THIS repository only
when its first segment is a tracked top-level entry. Everything else is counted
and printed, never flagged — the count is what tells you the gate's reach.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

LIBRARY_RELPATH = "packages/ai-hats-library/src/ai_hats_library"

CHECK = "prose-refs"

#: Composed, so the sentinel never appears whole here — spelled out, this file
#: opts ITSELF out (the trick is `check_adr_integrity.py`'s, HATS-1646).
OPT_OUT = f"{CHECK}: fixtures"
OPT_OUT_RE = re.compile(rf"^[ \t]*(?:#|//|<!--)?[ \t]*{re.escape(OPT_OUT)}", re.MULTILINE)

#: Prefixes that named the library before it moved under `packages/` (HATS-1437
#: moved it; the prose did not follow). A token opening with one of these is a
#: finding whether or not its tail resolves — the tail tells us which message.
STALE_LIBRARY_PREFIXES = ("library", "libraries")

#: Spellings that correctly denote the library root today, defined by the prose
#: itself (the `role-curator` injection defines `$LIB`, `rule_core_vs_usage_split` `<LIB>`).
LIVE_LIBRARY_PREFIXES = ("$LIB", "<LIB>")

PATH_SUFFIXES = frozenset(
    {".py", ".sh", ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".cfg", ".ini"}
)

FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
TICK_RE = re.compile(r"`([^`\n]{1,160})`")

#: The stale library prefix is exact enough to judge OUTSIDE backticks too — it
#: names a directory that does not exist, quoted or not. Nothing else is: an
#: unquoted slash in prose is usually prose (HATS-1825 found two such refs living
#: in `description:` frontmatter, where backticks are unconventional).
BARE_LIBRARY_RE = re.compile(
    #: The `-` in the lookbehind matters: without it the tail of
    #: `packages/ai-hats-library/src/...` reads as a bare `library/` reference.
    r"(?<![\w\-`/])((?:library|libraries)/[^\s,;:)\]`\"]+)"
)

#: `component § "Heading"` — as rigid a form as an ADR marker citation, and
#: checked the same way: the component resolves, then the heading must exist.
SECTION_RE = re.compile(r"`([a-z0-9][a-z0-9._-]*)`\s*§\s*[\"“]([^\"”\n]+)[\"”]")

#: `Class.method`, the one code-symbol shape prose actually uses.
SYMBOL_RE = re.compile(r"^([A-Z][A-Za-z0-9]*)\.([a-z_][a-z0-9_]*)$")

COMPONENT_DIRS = ("rules", "skills", "traits", "roles")

UNCOVERED = (
    "paths under `~` — outside the repository, so absence is not wrongness",
    "gitignored paths (`.agent/**`): runtime state, absent on a fresh clone",
    "unanchored paths — first segment is not a tracked top-level entry; the "
    "count is printed above, and a claim among them can only be checked by hand",
    "fenced code blocks — samples and commands, not claims about this tree",
    "prose claims with no reference shape at all — `[project.scripts]` in pyproject.toml is one",
    "a bare component name: no marker form separates `reflect-session` from "
    "`benchdiff` or `data-testid`, and every form tried read tool names as "
    "components. The `§` resolver below covers the one case that stayed exact",
    "unquoted references, except the stale `library/` prefix: only backticked "
    "spans are judged, because an unquoted slash in prose is usually prose",
    "behaviour: that a hook DOES what the prose says it does (HATS-1825 class A)",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    token: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: `{self.token}` — {self.message}"


def library_roots(root: Path) -> list[Path]:
    """The library root plus every composition layer under it.

    Layers are DISCOVERED, not listed: a layer this script had to be told about
    is a layer it silently never scans until someone remembers (HATS-1834). A
    layer is any child dir holding at least one component bucket.
    """
    lib = root / LIBRARY_RELPATH
    if not lib.is_dir():
        return []
    layers = sorted(
        child
        for child in lib.iterdir()
        if child.is_dir() and any((child / b).is_dir() for b in COMPONENT_DIRS)
    )
    return [lib, *layers]


def tracked_top_level(root: Path) -> frozenset[str]:
    """Top-level entries git tracks. The anchor set: a path claim about this
    repository opens with one of these, or we cannot say what it is relative to."""
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "ls-tree", "--name-only", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[{CHECK}] cannot read the git index: {exc}", file=sys.stderr)
        return frozenset()
    return frozenset(name.strip("/") for name in out.split() if name)


def gitignored(root: Path, rel: str) -> bool:
    try:
        return (
            subprocess.run(  # noqa: S603
                ["git", "-C", str(root), "check-ignore", "-q", rel],  # noqa: S607
                capture_output=True,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def expand_braces(pattern: str) -> list[str]:
    """`{core,usage}/roles/` -> both spellings. glob() has no brace syntax."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    out: list[str] = []
    for alt in match.group(1).split(","):
        out.extend(expand_braces(pattern[: match.start()] + alt + pattern[match.end() :]))
    return out


def matches(base: Path, pattern: str) -> bool:
    """Whether `pattern` (glob, `<...>` already wildcarded) hits anything under `base`."""
    pattern = pattern.strip("/") or "*"
    for spelling in expand_braces(pattern):
        try:
            if any(True for _ in base.glob(spelling)):
                return True
        except (ValueError, OSError, IndexError):
            return True  # an unglobbable spelling is not evidence of absence
    return False


def wildcarded(token: str) -> str:
    """`<name>` and `{name}` are placeholders the reader fills; a glob is their
    machine-readable form. `<ai_hats_dir>` is deliberately NOT special-cased —
    what it prefixes is runtime state, which this checker does not claim to see."""
    return re.sub(r"<[^>]+>", "*", token)


def is_path_shaped(token: str) -> bool:
    """Tight on purpose. `database/sql`, `n/a`, `task/<id>` and `samber/lo` all
    carry a slash and none is a path; requiring a suffix or a directory tail with
    at least two segments drops every one of them."""
    if not token or re.search(r"\s", token) or "@" in token:
        return False
    if token.startswith(("~", "http", "//", "$TMPDIR", "/")):
        return False
    if "/" not in token:
        return False
    if Path(token).suffix in PATH_SUFFIXES:
        return True
    stem = token.rstrip("/*")
    return (token.endswith("/") or token.endswith("/**")) and stem.count("/") >= 1


def split_prefix(token: str) -> tuple[str, str]:
    head, _, tail = token.partition("/")
    return head, tail


def check_library_alias(token: str, roots: list[Path]) -> str | None:
    """A library-rooted path, judged against where the library actually lives."""
    head, tail = split_prefix(token)
    if head in LIVE_LIBRARY_PREFIXES:
        if not tail or any(matches(r, wildcarded(tail)) for r in roots):
            return None
        return f"names nothing under {LIBRARY_RELPATH}/"
    if head not in STALE_LIBRARY_PREFIXES:
        return None
    if tail and any(matches(r, wildcarded(tail)) for r in roots):
        return (
            f"stale prefix: `{head}/` is not where the library lives — "
            f"`{tail}` resolves under {LIBRARY_RELPATH}/"
        )
    return f"stale prefix `{head}/`, and `{tail}` names nothing under {LIBRARY_RELPATH}/"


def check_repo_path(token: str, source: Path, root: Path, anchors: frozenset[str]) -> str | None:
    """An anchored repo path must resolve; an unanchored one is not judged."""
    normalised = token[2:] if token.startswith("./") else token
    head, _ = split_prefix(normalised)
    if head not in anchors:
        return None  # unanchored — counted by the caller, never flagged
    if gitignored(root, normalised.rstrip("*/") or normalised):
        return None
    pattern = wildcarded(normalised)
    if matches(root, pattern) or matches(source.parent, pattern):
        return None
    return "names nothing in this tree"


def strip_code_fences(text: str) -> list[tuple[int, str]]:
    """(lineno, line) for prose lines only. A sample is not a claim."""
    out, inside = [], False
    for lineno, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            inside = not inside
            continue
        if not inside:
            out.append((lineno, line))
    return out


def corpus(root: Path) -> list[Path]:
    lib = root / LIBRARY_RELPATH
    if not lib.is_dir():
        return []
    seen = {
        *lib.rglob("rules/*/rule.md"),
        *lib.rglob("skills/*/SKILL.md"),
        *lib.rglob("traits/*/config.yaml"),
        *lib.rglob("roles/*/config.yaml"),
    }
    return sorted(seen)


def scan_file(
    path: Path, root: Path, roots: list[Path], anchors: frozenset[str]
) -> tuple[list[Finding], int]:
    rel = path.relative_to(root).as_posix()
    findings: list[Finding] = []
    unanchored = 0
    for lineno, line in strip_code_fences(path.read_text(encoding="utf-8", errors="ignore")):
        for match in SECTION_RE.finditer(line):
            name, heading = match.group(1), match.group(2).strip()
            target = next(
                (r / d / name for r in roots for d in COMPONENT_DIRS if (r / d / name).is_dir()),
                None,
            )
            if target is None:
                continue  # the name itself is the marker resolver's business
            body = "\n".join(
                f.read_text(encoding="utf-8", errors="ignore")
                for f in target.rglob("*")
                if f.is_file() and f.suffix in {".md", ".yaml"}
            )
            if heading.lower() not in body.lower():
                note = f"`{name}` declares no such section"
                findings.append(Finding(rel, lineno, f"{name} § {heading}", note))
        for match in BARE_LIBRARY_RE.finditer(line):
            token = match.group(1).rstrip(".")
            if not is_path_shaped(token):
                continue
            message = check_library_alias(token, roots)
            if message:
                findings.append(Finding(rel, lineno, token, message))
        for match in TICK_RE.finditer(line):
            token = match.group(1).strip()
            symbol = SYMBOL_RE.match(token)
            if symbol:
                cls, method = symbol.groups()
                if not symbol_resolves(root, cls, method):
                    findings.append(
                        Finding(rel, lineno, token, f"no `{method}` on a class named `{cls}`")
                    )
                continue
            if not is_path_shaped(token):
                continue
            head, _ = split_prefix(token)
            if head in STALE_LIBRARY_PREFIXES or head in LIVE_LIBRARY_PREFIXES:
                message = check_library_alias(token, roots)
            else:
                message = check_repo_path(token, path, root, anchors)
                if message is None and head not in anchors and not token.startswith("./"):
                    unanchored += 1
            if message:
                findings.append(Finding(rel, lineno, token, message))
    return findings, unanchored


_SYMBOL_CACHE: dict[tuple[str, str], bool] = {}


def symbol_resolves(root: Path, cls: str, method: str) -> bool:
    """`Class.method` resolves when some file declaring `class Class` also
    declares `def method`. Co-location in one file is the cheap proxy for
    membership that needs no import of the tree under test."""
    key = (cls, method)
    if key in _SYMBOL_CACHE:
        return _SYMBOL_CACHE[key]
    class_re = re.compile(rf"^\s*class\s+{re.escape(cls)}\b", re.MULTILINE)
    def_re = re.compile(rf"^\s*(?:async\s+)?def\s+{re.escape(method)}\b", re.MULTILINE)
    found = False
    seen_class = False
    for source in [*(root / "src").rglob("*.py"), *(root / "packages").rglob("*.py")]:
        text = source.read_text(encoding="utf-8", errors="ignore")
        if not class_re.search(text):
            continue
        seen_class = True
        if def_re.search(text):
            found = True
            break
    # An unknown class is somebody else's vocabulary (`Path.cwd`), not a finding.
    _SYMBOL_CACHE[key] = found or not seen_class
    return _SYMBOL_CACHE[key]


def scan(root: Path) -> tuple[list[Finding], list[str], int, int]:
    roots = library_roots(root)
    anchors = tracked_top_level(root)
    findings: list[Finding] = []
    opted_out: list[str] = []
    unanchored = 0
    files = corpus(root)
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if OPT_OUT_RE.search(text):
            opted_out.append(path.relative_to(root).as_posix())
            continue
        file_findings, file_unanchored = scan_file(path, root, roots, anchors)
        findings.extend(file_findings)
        unanchored += file_unanchored
    ordered = sorted(findings, key=lambda f: (f.path, f.line))
    return ordered, sorted(opted_out), unanchored, len(files)


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    findings, opted_out, unanchored, files = scan(root)

    for finding in findings:
        print(f"[{CHECK}] FAIL: {finding}", file=sys.stderr)
    for path in opted_out:
        print(f"[{CHECK}] fixtures, not prose — skipped {path}", file=sys.stderr)

    scope = f"{files} library prose files; {unanchored} unanchored paths not judged"
    if findings:
        print(f"[{CHECK}] {len(findings)} unresolved references across {scope}", file=sys.stderr)
    else:
        print(f"[{CHECK}] ok: every checked reference resolves across {scope}", file=sys.stderr)
    for item in UNCOVERED:
        print(f"[{CHECK}] not covered: {item}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
