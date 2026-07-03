"""Composition value-types — the frozen result contract of role assembly.

Moved from ``ai_hats.composer`` in HATS-862 (ADR-0014: ``CompositionResult`` is
a core value-type, composed once by the integrator and injected DOWN into
packages). ``ComponentKind`` is deliberately narrower than the integrator's
``ComponentType`` taxonomy: composition results only ever carry rules and
skills (F3 ruling, HATS-862 plan.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path


class ComponentKind(str, Enum):
    """The kinds a resolved component can have inside a composition result."""

    RULE = "rule"
    SKILL = "skill"


@dataclass(frozen=True)
class ResolvedComponent:
    """A fully resolved component with its source path.

    HATS-452: frozen — once the composer has resolved a component, no layer
    is allowed to mutate its fields. Use ``dataclasses.replace`` (or, for
    `CompositionResult`, the explicit ``with_*`` methods) to produce a
    modified copy.
    """

    name: str
    component_type: ComponentKind
    source_path: Path
    injection: str = ""


@dataclass(frozen=True)
class CompositionResult:
    """The flattened result of composing a role.

    ``injections`` is the flat deduped view; ``trait_injections`` /
    ``role_injection`` / ``overlay_injection`` carry the same data with
    provenance for layered writers (HATS-282). Frozen (HATS-452): derive
    variants ONLY via the ``with_*`` methods — re-composing the same
    (role, overlays) pair for a variant is forbidden (ADR-0005 П1).
    """

    name: str
    priorities: list[str]
    rules: list[ResolvedComponent]
    skills: list[ResolvedComponent]
    injections: list[str]  # ordered injection texts
    errors: list[str] = field(default_factory=list)
    trait_injections: dict[str, str] = field(default_factory=dict)
    role_injection: str = ""
    overlay_injection: str = ""

    @property
    def merged_injection(self) -> str:
        """Concatenate all injections in dependency-tree order."""
        return "\n\n".join(inj for inj in self.injections if inj.strip())

    # ----- immutable transformations (HATS-452) -----

    def with_injection_override(self, text: str) -> "CompositionResult":
        """Return a copy whose ``injections`` is exactly ``[text]``.

        Sub-agent (Automate) path only — HITL has no override channel
        (ADR-0005 П2).
        """
        return replace(self, injections=[text])
