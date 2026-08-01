"""Composition value-types — the frozen result contract of role assembly.

Moved from ``ai_hats.composer`` in HATS-862 (ADR-0014: ``CompositionResult`` is
a core value-type, composed once by the integrator and injected DOWN into
packages). ``ComponentKind`` is deliberately narrower than the integrator's
``ComponentType`` taxonomy: composition results only ever carry rules and
skills (F3 ruling, HATS-862 plan.md).
"""

from __future__ import annotations

from collections.abc import Iterable
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
class ResolvedCheck:
    """One lifecycle binding, resolved to an absolute script (HATS-1140).

    Already fanned out: a row's ``on: [a, b]`` becomes two of these, so no
    consumer re-splits. ``script_path`` is absolute and comes from the declaring
    skill's ``source_path``, never from a provider's tree — that is what makes a
    binding fire identically under every surface (ADR-0019 D9).
    """

    skill: str
    script: str
    point: str
    on_error: str
    script_path: Path
    declared_by: str


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
    # Project-authored rule FILES, not library components — the composer is
    # project-agnostic, so these are attached downstream (HATS-1203).
    user_rules: tuple[Path, ...] = ()
    checks: tuple[ResolvedCheck, ...] = ()

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

    def with_user_rules(self, paths: "Iterable[Path]") -> "CompositionResult":
        """Return a copy carrying the project's user-rule files (HATS-1203).

        Attached by ``compose_for_role`` — the one composition site that knows
        ``project_dir`` — so every consumer of the result inherits them.
        """
        return replace(self, user_rules=tuple(paths))
