"""Invariant guard: src/ai_hats/paths/_env.py is the single reader of os.environ (HATS-1414)."""

from __future__ import annotations

from pathlib import Path


def test_paths_package_single_env_reader() -> None:
    """Verify that os.environ is only accessed in src/ai_hats/paths/_env.py."""
    paths_dir = Path(__file__).parent.parent / "src" / "ai_hats" / "paths"
    assert paths_dir.is_dir(), f"Paths directory not found at {paths_dir}"

    env_leaf = paths_dir / "_env.py"
    assert env_leaf.is_file(), f"_env.py leaf missing at {env_leaf}"

    violations: list[str] = []
    for py_file in sorted(paths_dir.glob("*.py")):
        if py_file.name == "_env.py":
            continue
        lines = py_file.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, start=1):
            if "os.environ" in line:
                violations.append(f"{py_file.name}:{idx}: {line.strip()}")

    assert not violations, (
        "os.environ found outside paths/_env.py in:\n" + "\n".join(violations)
    )
