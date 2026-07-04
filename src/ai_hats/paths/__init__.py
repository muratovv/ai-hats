"""Path conventions for ai-hats runtime + user config (HATS-316).

Package facade (HATS-831 split). The single ``paths.py`` module was split into
cohesive submodules — all re-exported here so ``from ai_hats.paths import X``
keeps working unchanged for every consumer:

  - :mod:`._dirs`      — directory-path resolution (``ai_hats_dir``, sessions/
    tracker/library dirs, venv + versioned-install layout, legacy migration map).
  - :mod:`.library`    — builtin ``library/`` SOURCE resolution (worktree-aware),
    the single home for ``files(LIBRARY_PKG)``.
  - :mod:`.claude`     — Claude Code ``.claude/*`` layout conventions (HATS-907/908).
  - :mod:`.gemini`     — Gemini CLI layout/channel conventions (HATS-908).
  - :mod:`.validation` — config-value + library-root validators.
  - :mod:`.constants`  — referenceable named constants for the above.

This package is a dependency-free leaf (``test_import_hygiene``): its submodules
import only stdlib, ``yaml``, and each other — never a higher-level ai-hats module.
"""

from __future__ import annotations

from .constants import *  # noqa: F403
from ._dirs import *  # noqa: F403
from .claude import *  # noqa: F403
from .gemini import *  # noqa: F403
from .validation import *  # noqa: F403
from .library import *  # noqa: F403
