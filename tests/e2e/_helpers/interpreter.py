"""Guard: the e2e interpreter must import the checkout under test (HATS-1218).

``ai_hats_shim`` execs ``sys.executable -m ai_hats``, and ``clean_env`` strips
``PYTHONPATH`` on purpose (HATS-685 — e2e must exercise the *installed*
artefact, not a source-tree shadow). In a git worktree those two rules combine
badly: the interpreter's editable install still points at the MAIN checkout, so
the whole e2e tier silently tests code you did not write. It goes green on
master's behaviour and red on yours, with nothing on screen to say why.

Setting ``PYTHONPATH`` is not the fix — it is denylisted by design. The fix is
to install the worktree and test that. So this module does not repair the
mismatch, it *refuses* it: the same principle the rest of HATS-1218 applies to
CLI flags — never accept an input you are going to ignore.

Deliberate long pitfall/contract module docstring — noqa: comment-length.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_PROBE = "import ai_hats, pathlib; print(pathlib.Path(ai_hats.__file__).resolve())"


def foreign_source_checkout(resolved_init: Path, repo_root: Path) -> Path | None:
    """The other checkout ``ai_hats`` resolves into, or ``None`` when fine.

    An editable install resolves to ``<checkout>/src/ai_hats/__init__.py``; a
    wheel install resolves inside ``site-packages`` and is the intended target
    of the e2e tier, so it never trips this.
    """
    src_dir = resolved_init.parent.parent
    if src_dir.name != "src":
        return None
    checkout = src_dir.parent
    return None if checkout == repo_root else checkout


def resolve_ai_hats_init(env: dict[str, str]) -> Path | None:
    """Where ``sys.executable -m ai_hats`` would import ``ai_hats`` from."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=env, check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip())


def remedy(repo_root: Path, foreign: Path) -> str:
    """Operator-facing message: what went wrong and the exact way out."""
    return (
        f"e2e would test the WRONG checkout.\n\n"
        f"  tests live in : {repo_root}\n"
        f"  but 'python -m ai_hats' imports from: {foreign}\n\n"
        f"{sys.executable} has an editable install pointing at another "
        f"checkout, and the e2e subprocess env strips PYTHONPATH by design "
        f"(HATS-685), so the source under test cannot win.\n\n"
        f"Fix — install THIS checkout into a throwaway venv and use it:\n"
        f"  uv venv /tmp/e2e-venv\n"
        f"  VIRTUAL_ENV=/tmp/e2e-venv uv pip install -e '{repo_root}[dev]'\n"
        f"  /tmp/e2e-venv/bin/python -m pytest tests/e2e/...\n\n"
        f"The pre-commit smoke hook takes pytest from PATH, so give it the "
        f"same interpreter:\n"
        f"  PATH=/tmp/e2e-venv/bin:$PATH git commit ..."
    )
