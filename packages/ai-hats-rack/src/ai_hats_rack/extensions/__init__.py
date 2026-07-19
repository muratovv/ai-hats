"""Stock rack extensions (K3, HATS-1022): plan scaffold + gate, frozen-pin
integrity, epic automation, derived views — pure (no integrator/wt/git imports).

Worktree/ownership adapters depend on the integrator's wt engine and live on
the integrator side (``ai_hats.rack_wiring``); the rack never imports them.
"""

from __future__ import annotations

from pathlib import Path

from ..definition import BacklogDefinition, load_backlog
from ..dispatch import Subscriber
from .epic import AUTOMATION_ACTOR, EpicAutomationExtension, decide
from .frozen import FrozenIntegrityExtension
from .lifecycle import ClearLifecycleHandler, StampLifecycleHandler
from .mirror import MirrorLinkHandler
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
    definition: BacklogDefinition | None = None,
) -> list[Subscriber]:
    """The standalone kit, composed from the backlog DEFINITION (HATS-1043): the
    packaged default declares frozen-integrity (ambient) + scaffold/plan-gate/
    stamp/clear (declaration-bound). Worktree/ownership ship with the integrator.

    ``definition`` defaults to the packaged tasks contract; the kit's handlers
    bind to the SAME edge product the kernel enforces (one source, HATS-1042)."""
    # Local import: composition imports extensions, so importing it at module
    # load would be a cycle — the standalone kit is the one consumer here.
    from ..composition import compose_subscribers, stock_factories

    defn = definition if definition is not None else load_backlog()
    return compose_subscribers(defn, tasks_dir, stock_factories(sections))


__all__ = [
    "AUTOMATION_ACTOR",
    "DEFAULT_PLAN_SECTIONS",
    "ClearLifecycleHandler",
    "DerivedViewsExtension",
    "EpicAutomationExtension",
    "FrozenIntegrityExtension",
    "MirrorLinkHandler",
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
