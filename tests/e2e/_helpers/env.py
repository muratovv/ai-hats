"""Subprocess env hygiene for e2e tests (HATS-685).

e2e install/launcher tests build a subprocess env and must exercise the REAL
installed ``ai_hats`` package, not the developer's source tree. The trap:
``src/ai_hats/`` has no ``library/`` subdir — ``library/`` maps to the
``ai_hats.library`` package only at BUILD time (``pyproject`` ``package-dir =
{"ai_hats.library": "library"}``). So an inherited ``PYTHONPATH=<repo>/src`` —
the standard worktree test workaround, and exactly what ``ai-hats wt exec``
sets — redirects a launcher subprocess's ``ai_hats`` import to the source tree,
where ``files("ai_hats.library")`` raises ``ModuleNotFoundError`` →
``_builtin_library_layers()`` returns ``[]`` → built-in roles vanish → "Role
'assistant' not found".

``ENV_DENYLIST`` is the set of python/ai_hats *redirect* vars that must never
leak into such a subprocess. The autouse fixture in ``conftest.py`` applies it
to ``os.environ`` for every e2e test, so any ``os.environ.copy()`` is clean by
construction. ``clean_env`` is the pure helper for call sites that prefer to be
explicit (and the unit-test surface).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Redirect vars that must not leak into a real-install e2e subprocess. PYTHONPATH
# is the proven culprit (HATS-685); the rest are defensive siblings that could
# redirect the interpreter or ai_hats config the same way.
ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "VIRTUAL_ENV",
        "AI_HATS_DIR",
        "AI_HATS_USER_HOME",
    }
)


def clean_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``base`` (default ``os.environ``) minus ``ENV_DENYLIST``.

    Pure: never mutates ``base``. Use when building a subprocess env that must
    run against the installed package rather than the source tree.
    """
    src = os.environ if base is None else base
    return {k: v for k, v in src.items() if k not in ENV_DENYLIST}
