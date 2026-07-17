"""Stock rack extensions (K3, HATS-1022): plan scaffold + gate, frozen-pin
integrity, epic automation, derived views — pure (no integrator/wt/git imports).

Worktree/ownership adapters depend on the integrator's wt engine and live on
the integrator side (``ai_hats.rack_wiring``); the rack never imports them.
"""

from __future__ import annotations

from pathlib import Path

from ..dispatch import Subscriber
from .epic import AUTOMATION_ACTOR, EpicAutomationExtension, decide
from .frozen import FrozenIntegrityExtension
from .plan import PlanGateExtension, PlanScaffoldExtension
from .sections import (
    DEFAULT_PLAN_SECTIONS,
    Section,
    SectionCatalogError,
    load_sections,
    merge_sections,
    render_scaffold,
    unfilled_sections,
)
from .views import DerivedViewsExtension


def standalone_extensions(
    tasks_dir: Path, sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS
) -> list[Subscriber]:
    """The standalone kit: frozen-integrity + scaffold + plan-gate (epic
    HATS-1014 §2.3, HATS-1031) — worktree/ownership ship with the integrator."""
    return [
        FrozenIntegrityExtension(tasks_dir),
        PlanGateExtension(tasks_dir, sections),
        PlanScaffoldExtension(tasks_dir, sections),
    ]


__all__ = [
    "AUTOMATION_ACTOR",
    "DEFAULT_PLAN_SECTIONS",
    "DerivedViewsExtension",
    "EpicAutomationExtension",
    "FrozenIntegrityExtension",
    "PlanGateExtension",
    "PlanScaffoldExtension",
    "Section",
    "SectionCatalogError",
    "decide",
    "load_sections",
    "merge_sections",
    "render_scaffold",
    "standalone_extensions",
    "unfilled_sections",
]
