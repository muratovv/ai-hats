"""Shared Assembler test helpers.

HATS-469 removed ``Assembler.bump()`` in favour of an explicit pipeline
composition in ``cli/assembly.py::do_bump``. Tests that used to call
``asm.bump(...)`` now go through :func:`bump_pipeline` — a thin
test-friendly re-composition of the same steps. Production code does
NOT use this helper (each call site composes the pipeline inline so
the steps stay visible at the call boundary).

The leading underscore in the module name keeps pytest from collecting
this file as a test module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_hats.assembler import Assembler
    from ai_hats.composer import CompositionResult


def bump_pipeline(
    asm: "Assembler",
    *,
    force_v07_migration: bool = False,
    check_v07_branches: bool = False,
    run_diagnostics: bool = True,
) -> "CompositionResult | None":
    """Mimic the pre-HATS-469 ``Assembler.bump()`` semantics.

    Composition steps (matches ``cli/assembly.py::do_bump``):

    1. ``_run_v07_migration`` — v0.6 → v0.7 layout heal (CLI-kwarg-gated).
    2. ``compose_for_role`` for the active role (or None).
    3. ``_refresh(install_time=True, result=...)`` — registry + heal +
       hooks.
    4. ``_run_diagnostics`` — orphan / empty-dir notes. Opt-out via
       ``run_diagnostics=False`` for tests that assert on stderr noise.

    Returns the composition result (or ``None`` when no role is active)
    — same contract as the old ``Assembler.bump()``.
    """
    from ai_hats.materialize import compose_for_role

    asm._run_v07_migration(
        force=force_v07_migration, check_branches=check_v07_branches,
    )
    cfg = asm.project_config
    role_name = cfg.active_role or cfg.default_role
    result = compose_for_role(asm, role_name) if role_name else None
    asm._refresh(install_time=True, result=result)
    if run_diagnostics:
        asm._run_diagnostics()
    return result
