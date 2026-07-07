"""Re-export shim — session artifact names live in ai_hats_observe (HATS-948, T15).

The names are observe's session-dir schema (``ai_hats_observe.artifacts``);
``ai_hats.paths`` re-exports them so integrator consumers (retro/cli/pipeline)
import unchanged (integrator → observe package, the allowed direction).
"""

from __future__ import annotations

from ai_hats_observe.artifacts import *  # noqa: F401,F403
from ai_hats_observe.artifacts import __all__  # noqa: F401  (re-bind so `import *` matches)
