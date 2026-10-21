#!/usr/bin/env python3
"""Walk production trees for an inert broad ``except``.

The decision lives in ``silent_fallback_gate.violations``. This script is the
CI entry: it finds the files and prints what that function returns.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_GATE = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/usage/skills"
    / "silent-fallback-gate/hooks/silent_fallback_gate.py"
)

# Production trees only. Silence inside a test hides a broken test, not a
# product defect, and the two want different remedies.
SOURCE_GLOBS = ("src/**/*.py", "packages/**/*.py", "scripts/*.py")


def _load_gate():
    spec = importlib.util.spec_from_file_location("silent_fallback_gate", _GATE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_GATE}")
    module = importlib.util.module_from_spec(spec)
    # dataclass resolves the class module through sys.modules during exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_gate = _load_gate()
violations = _gate.violations
MARKER = _gate.MARKER


def source_files(root: Path) -> list[Path]:
    seen = {p for glob in SOURCE_GLOBS for p in root.glob(glob)}
    return sorted(p for p in seen if "tests" not in p.relative_to(root).parts)


def scan(root: Path) -> list:
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
