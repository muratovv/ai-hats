"""Back-compat shim — the task FSM moved to ai_hats_tracker (HATS-933)."""

from ai_hats_tracker.state import (  # noqa: F401
    PLAN_SCAFFOLD,
    PLAN_SECTIONS,
    EmptyPlanError,
    Section,
    TaskManager,
    TaskTransition,
    WorktreeEffects,
    render_plan_scaffold,
)
