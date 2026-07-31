"""Invariant guard: src/ai_hats/env.py is the single reader of os.environ for paths (HATS-1414)."""

from __future__ import annotations

from pathlib import Path


def test_paths_package_single_env_reader() -> None:
    """Verify that os.environ is only accessed in src/ai_hats/env.py across paths."""
    src_dir = Path(__file__).parent.parent / "src" / "ai_hats"
    paths_dir = src_dir / "paths"
    assert paths_dir.is_dir(), f"Paths directory not found at {paths_dir}"

    env_leaf = src_dir / "env.py"
    assert env_leaf.is_file(), f"env.py leaf missing at {env_leaf}"

    violations: list[str] = []
    for py_file in sorted(paths_dir.glob("*.py")):
        lines = py_file.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, start=1):
            if "os.environ" in line:
                violations.append(f"paths/{py_file.name}:{idx}: {line.strip()}")

    assert not violations, "os.environ found outside env.py in paths:\n" + "\n".join(violations)
