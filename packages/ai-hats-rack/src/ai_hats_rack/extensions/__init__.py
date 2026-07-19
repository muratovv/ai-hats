"""Stock rack extensions (K3, HATS-1022): plan scaffold + gate, frozen-pin
integrity, epic automation, derived views — pure (no integrator/wt/git imports).

Worktree/ownership adapters depend on the integrator's wt engine and live on
the integrator side (``ai_hats.rack_wiring``); the rack never imports them.
"""

from __future__ import annotations

from pathlib import Path

from ..dispatch import Subscriber
from ..fsm import Topology
from .epic import AUTOMATION_ACTOR, EpicAutomationExtension, decide
from .frozen import FrozenIntegrityExtension
from .lifecycle import ClearLifecycleHandler, StampLifecycleHandler
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
    tasks_dir: Path,
    sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS,
    *,
    topology: Topology | None = None,
) -> list[Subscriber]:
    """The standalone kit: frozen-integrity + scaffold + plan-gate (epic
    HATS-1014 §2.3, HATS-1031) — worktree/ownership ship with the integrator.

    ``topology`` is threaded from the resolved backlog definition so the kit's
    subscribers subscribe to the SAME edge product the kernel enforces, never a
    separately default-loaded copy (HATS-1042)."""
    return [
        FrozenIntegrityExtension(tasks_dir, topology=topology),
        PlanGateExtension(tasks_dir, sections, topology=topology),
        PlanScaffoldExtension(tasks_dir, sections, topology=topology),
    ]


__all__ = [
    "AUTOMATION_ACTOR",
    "DEFAULT_PLAN_SECTIONS",
    "ClearLifecycleHandler",
    "DerivedViewsExtension",
    "EpicAutomationExtension",
    "FrozenIntegrityExtension",
    "PlanGateExtension",
    "PlanScaffoldExtension",
    "StampLifecycleHandler",
    "Section",
    "SectionCatalogError",
    "decide",
    "load_sections",
    "merge_sections",
    "render_scaffold",
    "standalone_extensions",
    "unfilled_sections",
]
