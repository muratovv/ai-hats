"""Standalone session-browse CLI for ``ai_hats_observe`` (HATS-952).

The ``session`` Click group (``list`` / ``show`` / ``audit``) runs on the
worktree-free ``STANDALONE`` host so the package browses recorded sessions with
only ai-hats-core — no ``ai-hats.yaml``, no composition. The ai-hats integrator
imports the group, attaches its own ``Host`` (AI_HATS_DIR/yaml-aware resolvers)
at mount, and re-attaches the retro subcommands (``retro`` / ``retro-validate``,
downstream consumers that stay integrator-side).
"""

from ._host import STANDALONE, Host, attach, host

__all__ = ["STANDALONE", "Host", "attach", "host"]
