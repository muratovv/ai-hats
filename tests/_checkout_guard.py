"""Shared guard: verify the test session measures the checkout under test.

Two independent axes — a run can be wrong on either one alone:

- **Package** (HATS-1242): an editable install pointing at MAIN makes
  `import ai_hats` inside a worktree resolve to MAIN's source.
- **Library layers** (HATS-1429): the package resolves right while the composed
  `library/` layers still come from MAIN, because `AI_HATS_PROJECT_DIR` outranks
  cwd auto-detection in `builtin_library_root()`.
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


def _library_source_checkout(layer: Path) -> Path | None:
    """The source checkout serving ``layer``, or None if it is not a source layout.

    Mirrors ``_SOURCE_LIBRARY_SUBPATHS`` in ``ai_hats.paths.library``: the layers live
    under ``<checkout>/packages/ai-hats-library/src/ai_hats_library`` (monorepo or
    worktree) or ``<checkout>/src/ai_hats_library`` (standalone git-split checkout).
    """
    for pkg in layer.parents:
        if pkg.name != "ai_hats_library":
            continue
        parents = pkg.parents
        if len(parents) >= 4 and parents[0].name == "src" and parents[1].name == "ai-hats-library":
            return parents[3]
        if len(parents) >= 2 and parents[0].name == "src":
            return parents[1]
        return None
    return None


def foreign_library_layers(layers: list[Path], repo_root: Path) -> Path | None:
    """Return the foreign checkout serving ``layers``, else None.

    An installed wheel (site-packages) is a legitimate downstream resolution, not a
    wrong checkout; empty layers mean a broken install, which is the package guard's
    problem rather than this one's.
    """
    repo_root = repo_root.resolve()

    for layer in layers:
        resolved = layer.resolve()
        if resolved.is_relative_to(repo_root):
            continue
        checkout = _library_source_checkout(resolved)
        if checkout is not None and checkout != repo_root:
            return checkout
    return None


def library_remedy_message(repo_root: Path, foreign: Path) -> str:
    """Operator-facing message for a library-layer split (HATS-1429)."""
    return (
        f"Test suite would compose the WRONG library (HATS-1429).\n\n"
        f"  tests live in                 : {repo_root}\n"
        f"  but library layers resolve in : {foreign}\n\n"
        f"The 'ai_hats' package may well be correct here — this is the other axis:\n"
        f"the composed library/ comes from a checkout you are not testing, so every\n"
        f"role, trait and skill under test is someone else's.\n\n"
        f"conftest already drops the AI_HATS_PROJECT_DIR / AI_HATS_DIR pin at import,\n"
        f"so one of these is in play:\n\n"
        f"  * AI_HATS_LIBRARY_ROOT points elsewhere — unset it, or set it to\n"
        f"      {repo_root}/packages/ai-hats-library/src/ai_hats_library\n"
        f"  * pytest was launched from another checkout's cwd — cd into\n"
        f"      {repo_root}\n"
        f"    first, since cwd is what resolution falls back to.\n\n"
        f"To force execution against the foreign library (not recommended):\n"
        f"  export {ENV_IGNORE_FOREIGN_CHECKOUT}=1"
    )


def check_library_integrity(layers: list[Path], repo_root: Path) -> None:
    """Assert the composed library layers come from repo_root, unless ignored."""
    if os.environ.get(ENV_IGNORE_FOREIGN_CHECKOUT) == "1":
        return

    foreign = foreign_library_layers(layers, repo_root)
    if foreign is not None:
        raise RuntimeError(library_remedy_message(repo_root, foreign))
