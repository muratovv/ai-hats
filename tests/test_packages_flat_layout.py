"""HATS-1826 — a workspace member is ``packages/<member>``, never deeper.

Four consumers read the workspace ONE level deep. Until this task
``packages/surfaces/*`` nested one deeper and all four silently skipped those
four distributions. The nesting is gone; this gate is what keeps it gone, so the
four stay one-level instead of each growing its own walk (ADR-0026 D3).

Reads via ``git ls-files``, tracked plus untracked-but-not-ignored: a gitignored
``.venv`` inside a member cannot fire it, an uncommitted nested member does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every reader that stops at ``packages/<member>``. Named in the failure
#: message because the cost of nesting is not "this test fails", it is "these
#: four go quiet".
ONE_LEVEL_CONSUMERS = (
    "tests/test_package_version_drift.py — glob('packages/*/pyproject.toml')",
    "tests/test_release_packages_coverage.py — glob('packages/*/pyproject.toml')",
    "scripts/check_pkg_version_skew.py — run(): packages/<dir>/pyproject.toml",
    "scripts/ai-hats-launcher — probe: packages/*/src/*/__init__.py",
)


def _pyprojects_under_packages() -> list[str]:
    """Repo-relative path of every non-ignored ``pyproject.toml`` under ``packages/``."""
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "packages"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(p for p in proc.stdout.splitlines() if p.endswith("/pyproject.toml"))


def test_no_pyproject_nests_below_a_workspace_member() -> None:
    # "packages/<member>/pyproject.toml" — anything else is a nested member.
    nested = [p for p in _pyprojects_under_packages() if p.count("/") != 2]

    assert not nested, (
        f"pyproject.toml nested below packages/<member>: {nested}. A workspace member "
        "must sit exactly one level under packages/ — these consumers read only that "
        "depth and would skip the package in SILENCE:\n  "
        + "\n  ".join(ONE_LEVEL_CONSUMERS)
        + "\nFlatten the member (packages/<name>/) rather than deepening the four walks."
    )
