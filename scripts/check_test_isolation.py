#!/usr/bin/env python3
"""A test that patches the unit under test is reporting a missing parameter.

HATS-1599. Per test file it holds a **ratchet** over the calls that replace
something inside the code under test, and refuses any count that no longer
matches the recorded baseline. The criteria for what to do about a red count
ride in ``EXITS`` below rather than in a rule or a skill: the gate fires exactly
where the decision is made, and a second copy in the prompt would only be a
copy — the whole reason this check is a linter and not a component.

`setenv` / `delenv` are deliberately uncounted: an entry point whose job is to
read the environment is tested by setting it, so counting them would penalize
the very shape exit 1 asks for.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Calls that replace something inside the unit under test. `chdir` earns its
# place: needing it means the code resolved a location it should have been told.
PATCH_METHODS = frozenset({"setattr", "delattr", "setitem", "delitem", "chdir"})

# Every test tree in the workspace; `surfaces/*` nest one level deeper, and an
# area keeps its own tests inside the package (ADR-0026 D5) — without that last
# glob, moving a test into its area would be a way out of this ratchet.
TEST_GLOBS = (
    "tests/**/*.py",
    "src/ai_hats/*/tests/**/*.py",
    "src/ai_hats/*/*/tests/**/*.py",  # the surfaces area nests one deeper (HATS-1826)
    "packages/*/tests/**/*.py",
    "packages/*/*/tests/**/*.py",
)

# The gate fires exactly where the decision is made, so the decision travels
# with it — printed once per run, not once per offending file.
EXITS = """
  Each patch above takes one of three exits:
    1. INJECT — the unit fetched what you wanted to replace (its own import, an
       ambient read at depth). Make it a keyword-only parameter and resolve the
       real default at a composition root, never three frames below it.
    2. EXEMPT — the site tests an entry point whose job IS reading the
       environment. `setenv` there is correct and is not counted at all.
    3. RECORD — a third-party boundary you do not own, with no seam worth
       building. Raise the number in the baseline BY HAND and say which exit you
       took in the commit; that hand edit is what a reviewer sees.
  "It would be a big refactor" is not an exit — that is the finding, as an excuse.
"""


@dataclass(frozen=True)
class Violation:
    path: str  # repo-relative
    measured: int
    recorded: int  # 0 when the baseline has no entry

    def __str__(self) -> str:
        if self.measured > self.recorded:
            return (
                f"{self.path}: patches the code under test {self.measured}x, "
                f"baseline {self.recorded}"
            )
        return (
            f"{self.path}: down to {self.measured}x from a baseline of {self.recorded} — "
            f"re-cut the ratchet (`scripts/check_test_isolation.py --update`) so the "
            f"slack cannot be spent later"
        )


def compare(measured: Mapping[str, int], baseline: Mapping[str, int]) -> list[Violation]:
    """Every file whose count no longer equals what the baseline recorded."""
    found = [
        Violation(path, measured.get(path, 0), baseline.get(path, 0))
        for path in set(measured) | set(baseline)
        if measured.get(path, 0) != baseline.get(path, 0)
    ]
    return sorted(found, key=lambda v: v.path)


def patch_calls(source: str) -> int:
    """How many times one test module patches the code it is testing."""
    tree = ast.parse(source)
    prefixes = _patch_prefixes(tree)
    return sum(1 for node in ast.walk(tree) if _is_patch_call(node, prefixes))


def _patch_prefixes(tree: ast.AST) -> frozenset[tuple[str, ...]]:
    """Dotted prefixes naming `unittest.mock.patch` under this module's imports.

    Import-derived, so an HTTP client's `.patch()` and a local helper of the
    same name are not mistaken for one.
    """
    prefixes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module == "unittest.mock" and alias.name == "patch":
                    prefixes.add((alias.asname or "patch",))
                elif node.module == "unittest" and alias.name == "mock":
                    prefixes.add((alias.asname or "mock", "patch"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "unittest.mock":
                    root = (alias.asname,) if alias.asname else ("unittest", "mock")
                    prefixes.add((*root, "patch"))
    return frozenset(prefixes)


def _is_patch_call(node: ast.AST, patch_prefixes: frozenset[tuple[str, ...]]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    parts = _dotted(node.func)
    if not parts:
        return False
    # `monkeypatch.setattr(...)`, `os.chdir(...)` — a receiver and a method.
    if len(parts) == 2 and parts[1] in PATCH_METHODS:
        return True
    # `patch(...)`, `patch.object(...)`, `mock.patch.dict(...)`.
    return any(tuple(parts[: len(prefix)]) == prefix for prefix in patch_prefixes)


def _dotted(func: ast.expr) -> list[str]:
    """`mock.patch.object` -> ['mock', 'patch', 'object']; [] if not a dotted name."""
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return []
    parts.append(func.id)
    return parts[::-1]


def test_files(root: Path) -> list[Path]:
    return sorted({path for glob in TEST_GLOBS for path in root.glob(glob)})


def scan(root: Path) -> dict[str, int]:
    """Patch count per test file; files that patch nothing stay out of the map."""
    counted = {}
    for path in test_files(root):
        count = patch_calls(path.read_text())
        if count:
            counted[path.relative_to(root).as_posix()] = count
    return counted


def baseline_path(root: Path) -> Path:
    return root / "scripts" / "test_isolation_baseline.json"


def load_baseline(path: Path) -> dict[str, int]:
    return json.loads(path.read_text()) if path.exists() else {}


def write_baseline(path: Path, counts: Mapping[str, int]) -> None:
    body = {name: counts[name] for name in sorted(counts)}
    path.write_text(json.dumps(body, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    update = "--update" in argv
    positional = [arg for arg in argv if not arg.startswith("-")]
    root = Path(positional[0]).resolve() if positional else REPO_ROOT

    measured = scan(root)
    path = baseline_path(root)
    found = compare(measured, load_baseline(path))
    rises = [v for v in found if v.measured > v.recorded]

    if update:
        for violation in rises:
            print(f"[test-isolation] REFUSED: {violation}", file=sys.stderr)
        if rises:
            print(
                "[test-isolation] --update only lowers. Recording a rise is a "
                "hand edit, so a reviewer sees it.",
                file=sys.stderr,
            )
            return 2
        write_baseline(path, measured)
        print(f"[test-isolation] baseline re-cut: {sum(measured.values())} calls", file=sys.stderr)
        return 0

    for violation in found:
        print(f"[test-isolation] FAIL: {violation}", file=sys.stderr)
    if any(v.measured > v.recorded for v in found):
        print(EXITS, file=sys.stderr)
    if not found:
        print(
            f"[test-isolation] ok: {sum(measured.values())} calls across "
            f"{len(measured)} files, exactly the recorded baseline",
            file=sys.stderr,
        )
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
