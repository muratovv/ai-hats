#!/usr/bin/env python3
"""A broad ``except`` whose body can do nothing is a defect no test can see.

HATS-1373. The defect is not the *breadth* of the catch — most broad handlers
here report through a logger, a domain call, or a returned value. It is a body
that is **inert**: no call and no ``raise``, so nothing in it can carry the
failure outward. That needs no dictionary of emission shapes to decide, which is
why "does it log?" was not reproducible as a definition. Silence is occasionally
correct — say so with ``# silent-ok: <reason>`` and the site is accepted.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MARKER = "# silent-ok:"

# Production trees only. Silence inside a test hides a broken test, not a
# product defect, and the two want different remedies.
SOURCE_GLOBS = ("src/**/*.py", "packages/**/*.py", "scripts/*.py")

BROAD = frozenset({"Exception", "BaseException"})


@dataclass(frozen=True)
class Violation:
    path: str  # repo-relative
    line: int
    source: str  # the `except ...:` line, stripped

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line}: `{self.source}` catches broadly and its body "
            f"neither raises nor calls anything — the failure cannot reach a log, a "
            f"test, or a caller. Report it, re-raise it, or mark the site "
            f"`{MARKER} <reason>`."
        )


def is_broad(handler: ast.ExceptHandler) -> bool:
    """A bare ``except``, or one naming Exception/BaseException."""
    if handler.type is None:
        return True
    caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for node in caught:
        name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
        if name in BROAD:
            return True
    return False


def is_inert(handler: ast.ExceptHandler) -> bool:
    """Nothing in the body can carry the failure outward.

    No call and no ``raise`` — and no use of the bound exception either, since
    ``return False, f"broken: {exc}"`` reports through its return value without
    calling anything.
    """
    for statement in handler.body:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Call, ast.Raise)):
                return False
            if handler.name and isinstance(node, ast.Name) and node.id == handler.name:
                return False
    return True


def is_marked(handler: ast.ExceptHandler, lines: list[str]) -> bool:
    """``# silent-ok:`` anywhere in the handler's line span."""
    return any(MARKER in line for line in lines[handler.lineno - 1 : handler.end_lineno])


def violations(source: str, path: str) -> list[Violation]:
    """Every inert broad handler in one module."""
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if is_broad(node) and is_inert(node) and not is_marked(node, lines):
            found.append(Violation(path, node.lineno, lines[node.lineno - 1].strip()))
    return sorted(found, key=lambda v: (v.path, v.line))


def source_files(root: Path) -> list[Path]:
    seen = {p for glob in SOURCE_GLOBS for p in root.glob(glob)}
    return sorted(p for p in seen if "tests" not in p.relative_to(root).parts)


def scan(root: Path) -> list[Violation]:
    found = []
    for path in source_files(root):
        found.extend(violations(path.read_text(), path.relative_to(root).as_posix()))
    return found


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    found = scan(root)
    for violation in found:
        print(f"[silent-fallback] FAIL: {violation}", file=sys.stderr)
    if not found:
        print(
            f"[silent-fallback] ok: every broad handler across "
            f"{len(source_files(root))} modules reports, raises, or is marked",
            file=sys.stderr,
        )
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
