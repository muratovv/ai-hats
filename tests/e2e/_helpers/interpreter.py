"""Guard: the e2e interpreter must import the checkout under test (HATS-1218, HATS-1242).

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

from tests._checkout_guard import (
    foreign_source_checkout as foreign_source_checkout,
)
from tests._checkout_guard import (
    remedy_message as remedy,
)

__all__ = ["foreign_source_checkout", "remedy", "resolve_ai_hats_init"]

_PROBE = "import ai_hats, pathlib; print(pathlib.Path(ai_hats.__file__).resolve())"


def resolve_ai_hats_init(env: dict[str, str]) -> Path | None:
    """Where ``sys.executable -m ai_hats`` would import ``ai_hats`` from."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip())
