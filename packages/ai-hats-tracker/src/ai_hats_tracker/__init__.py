"""Standalone task-card schema + worktree-free task FSM (ADR-0014 Phase 2, T16).

The self-contained tracker core: the ``TaskCard`` schema and the ``TaskManager``
state machine driving ``brainstorm → … → done`` through the injected
``WorktreeEffects`` seam (default ``None`` = a pure, worktree-free FSM). The
one-directional import rule forbids importing the ``ai_hats`` integrator or
``ai_hats_wt``; ai-hats imports *from* here, never the reverse. ``__all__`` is
the public surface a standalone consumer drives.
"""

from __future__ import annotations

from .layout import TrackerPaths
from .models import Attachment, TaskCard, TaskState, WorkLogEntry
from .plan_extract import Candidate, extract_candidates, mark_extracted
from .state import (
    PLAN_SCAFFOLD,
    PLAN_SECTIONS,
    EmptyPlanError,
    TaskManager,
    WorktreeEffects,
)

__all__ = [
    "Attachment",
    "Candidate",
    "EmptyPlanError",
    "PLAN_SCAFFOLD",
    "PLAN_SECTIONS",
    "TaskCard",
    "TaskManager",
    "TaskState",
    "TrackerPaths",
    "WorkLogEntry",
    "WorktreeEffects",
    "extract_candidates",
    "mark_extracted",
]
