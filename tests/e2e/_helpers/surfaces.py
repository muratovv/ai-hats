"""Every executable an e2e PATH entry must carry (HATS-1847).

A test that puts a directory first on PATH and then invokes a surface by bare
name is measuring whatever that directory does NOT hold. Until now the directory
held only ``ai-hats``, so ``rack`` came from the ambient PATH — in a worktree,
the MAIN checkout's console script, whose shebang is the main venv's interpreter.
With ``PYTHONPATH`` pointing at the worktree's ``src``, that is metadata from one
tree against code from another. HATS-1826 paid 47 failures to find it, and only
because a rename finally made the skew fatal.

Every surface here runs ``sys.executable`` — the one interpreter
``_guard_interpreter_matches_checkout`` already proves imports this checkout — so
the whole PATH entry is the checkout under test by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Surface name on PATH → the module ``python -m`` runs for it. Data, and the
#: consent registry declares its surfaces without reading this table, so
#: ``tests/e2e_harness/test_checkout_bin.py`` is what stops the two from drifting.
SURFACES: dict[str, str] = {
    # HATS-790 removed the console script, so a shim is the only spelling there is.
    "ai-hats": "ai_hats",
    "rack": "ai_hats_rack",
}


def write_surface_shims(bin_dir: Path) -> Path:
    """Materialise every surface in ``bin_dir`` and return it."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, module in SURFACES.items():
        executable = bin_dir / name
        executable.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" -m {module} "$@"\n')
        executable.chmod(0o755)
    return bin_dir
