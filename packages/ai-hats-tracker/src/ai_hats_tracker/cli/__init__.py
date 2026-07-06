"""Standalone backlog CLI for ``ai_hats_tracker`` (HATS-934).

The ``task`` / ``attach`` Click groups run on the wt-free ``_seam`` defaults so
the package drives create/list/show/transition/log/link/plan-extract/attach with
only ai-hats-core (``ai-hats-wt`` is an optional extra). The ai-hats integrator
imports these groups and overrides ``_seam`` with its wt-wired helpers at mount.
"""
