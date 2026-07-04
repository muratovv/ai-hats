"""Workspace-aware PYTHONPATH construction (HATS-913).

``src`` alone Franken-mixes a workspace checkout: a subprocess gets the
checkout's integrator while ``packages/*`` resolve via the venv's editable
installs — the MAIN checkout's code. Prepending every ``packages/*/src``
(mirroring pyproject ``tool.pytest.ini_options.pythonpath``) makes a worktree
run test the worktree's packages too.
"""

from __future__ import annotations

import os
from pathlib import Path


def workspace_pythonpath(root: Path, existing: str = "") -> str:
    """PYTHONPATH that runs the checkout at ``root`` end-to-end.

    ``<root>/src``, then each ``<root>/packages/*/src`` (sorted), then
    ``existing`` (dropped when empty), joined with ``os.pathsep``.
    """
    roots = [root / "src", *sorted((root / "packages").glob("*/src"))]
    parts = [str(r) for r in roots]
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)
