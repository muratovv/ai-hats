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

    `injections` is the legacy flat view (trait/role/overlay text, deduped by
    content). The structured fields carry the same data with provenance for
    layered writers (HATS-282):

    - `trait_injections` — `{trait_name: text}`, deduped by text: a trait whose
      text is empty or already recorded elsewhere is absent.
    - `role_injection` / `overlay_injection` — root role's / overlay's own text
      (independent of dedup; recorded if non-empty).

    Rules and skills carry provenance via `rules`/`skills` (deduped by name).

    HATS-452 immutability contract: ``frozen=True``, so fields cannot be
    reassigned. Deriving a *modified* result (e.g. an injection override for a
    sub-agent) MUST go through the ``with_*`` methods; re-composing the same
    (role, overlays) pair to obtain a variant is forbidden (П1 in ADR-0005).
    ``frozen`` guards field reassignment only — the inner list/dict containers
    stay technically mutable, but by convention callers never mutate them in
    place (the composer builds them once during ``compose``).
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
        """Return a copy whose ``injections`` is replaced by a single entry.

        Used by sub-agent path (HATS-267) to inject a caller-supplied prompt
        in place of the composed role text while preserving the rest of the
        composition (rules/skills/hooks/priorities). The new injections list
        contains exactly the override text; pass an empty string to
        explicitly clear (rare — the consumer-side filter will then emit no
        injection section).

        Per П2 in ADR-0005, this method is intended ONLY for the Automate
        path (``SubAgentRunner``). HITL (``WrapRunner``) does not have an
        override channel and must not call this.
        """
        return replace(self, injections=[text])
