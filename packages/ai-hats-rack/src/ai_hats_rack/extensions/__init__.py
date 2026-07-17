"""Stock rack extensions (K3, HATS-1022): plan scaffold + gate, epic
automation, derived views — pure (no integrator/wt/git imports).

Worktree/ownership adapters depend on the integrator's wt engine and live on
the integrator side (``ai_hats.rack_wiring``); the rack never imports them.
"""

from __future__ import annotations

from pathlib import Path

from ..dispatch import Subscriber
from .epic import AUTOMATION_ACTOR, EpicAutomationExtension, decide
from .plan import PlanGateExtension, PlanScaffoldExtension
from .sections import (
    DEFAULT_PLAN_SECTIONS,
    Section,
    SectionCatalogError,
    load_sections,
    render_scaffold,
    unfilled_sections,
)
from .views import DerivedViewsExtension


def standalone_extensions(
    tasks_dir: Path, sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS
) -> list[Subscriber]:
    """The standalone kit: scaffold + plan-gate only (epic HATS-1014 §2.3) —
    worktree/ownership ship with the integrator."""
    return [
        PlanGateExtension(tasks_dir, sections),
        PlanScaffoldExtension(tasks_dir, sections),
    ]


__all__ = [
    "AUTOMATION_ACTOR",
    "DEFAULT_PLAN_SECTIONS",
    "DerivedViewsExtension",
    "EpicAutomationExtension",
    "PlanGateExtension",
    "PlanScaffoldExtension",
    "Section",
    "SectionCatalogError",
    "decide",
    "load_sections",
    "render_scaffold",
    "standalone_extensions",
    "unfilled_sections",
]
