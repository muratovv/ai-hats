"""Shared guard: verify python environment imports the checkout under test (HATS-1242).

In a git worktree, a python environment (e.g. root .venv) may hold an editable install
pointing at the MAIN checkout. Importing `ai_hats` inside a worktree silently resolves
to source files from MAIN instead of the worktree.

This module provides the core detection and remedy formatting for both:
- Root pytest autouse fixture (`tests/conftest.py`)
- E2E interpreter pre-flight guard (`tests/e2e/_helpers/interpreter.py`)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_IGNORE_FOREIGN_CHECKOUT = "AI_HATS_IGNORE_FOREIGN_CHECKOUT"


def foreign_source_checkout(resolved_init: Path, repo_root: Path) -> Path | None:
    """Return the foreign checkout path if ai_hats resolves outside repo_root, else None.

    An editable install resolves to ``<checkout>/src/ai_hats/__init__.py``.
    A wheel install resolves inside ``site-packages`` and is not considered a foreign
    source checkout.
    """
    resolved_init = resolved_init.resolve()
    repo_root = repo_root.resolve()

    src_dir = resolved_init.parent.parent
    if src_dir.name != "src":
        return None

    checkout = src_dir.parent
    return None if checkout == repo_root else checkout


def discover_subpackages(repo_root: Path) -> list[Path]:
    """Find all sub-packages with pyproject.toml under packages/ relative to repo_root."""
    packages_dir = repo_root / "packages"
    if not packages_dir.is_dir():
        return []
    return sorted([p.parent for p in packages_dir.glob("**/pyproject.toml")])


def remedy_message(repo_root: Path, foreign: Path) -> str:
    """Operator-facing message: what went wrong and how to fix it."""
    subpkgs = discover_subpackages(repo_root)
    pkg_args = " \\\n    ".join(f"-e '{p.relative_to(repo_root)}'" for p in subpkgs)
    if pkg_args:
        pkg_args = " \\\n    " + pkg_args

    return (
        f"Test suite would test the WRONG checkout (HATS-1242).\n\n"
        f"  tests live in             : {repo_root}\n"
        f"  but 'ai_hats' imports from: {foreign}\n\n"
        f"{sys.executable} has an editable install pointing at another "
        f"checkout, so the source under test cannot win.\n\n"
        f"Fix — step 1, create and provision a dedicated worktree venv using uv:\n"
        f"  uv venv .venv\n"
        f"  VIRTUAL_ENV=.venv uv pip install -e '.[dev]'{pkg_args}\n\n"
        f"Fix — step 2, put that venv on PATH. Step 1 alone is NOT enough for a\n"
        f"'pytest' you type yourself: the bare name still resolves through PATH to\n"
        f"the interpreter above, so the next run trips this guard again\n"
        f"(HATS-1245). The git hooks no longer need this step — they take the\n"
        f"committed checkout's own .venv (HATS-1291/1314). Either for this shell:\n"
        f'  export PATH="{repo_root}/.venv/bin:$PATH"\n'
        f"or for a single command:\n"
        f'  PATH="{repo_root}/.venv/bin:$PATH" pytest ...\n\n'
        f"To force execution against the foreign checkout (not recommended):\n"
        f"  export {ENV_IGNORE_FOREIGN_CHECKOUT}=1"
    )


def check_checkout_integrity(resolved_init: Path | None, repo_root: Path) -> None:
    """Assert that ai_hats resolves to repo_root unless foreign checkout check is ignored."""
    if os.environ.get(ENV_IGNORE_FOREIGN_CHECKOUT) == "1":
        return

    if resolved_init is None:
        return

    foreign = foreign_source_checkout(resolved_init, repo_root)
    if foreign is not None:
        raise RuntimeError(remedy_message(repo_root, foreign))
