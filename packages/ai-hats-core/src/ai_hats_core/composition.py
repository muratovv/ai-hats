"""Composition value-types — the frozen result contract of role assembly.

Moved from ``ai_hats.composer`` in HATS-862 (ADR-0014: ``CompositionResult`` is
a core value-type, composed once by the integrator and injected DOWN into
packages). ``ComponentKind`` is deliberately narrower than the integrator's
``ComponentType`` taxonomy: composition results only ever carry rules and
skills (F3 ruling, HATS-862 plan.md).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any


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
class ConsentPoint:
    """One point a component declared needs the supervisor's explicit approval.

    ``app`` is ``consent_gate``; ``path`` names the wrapped operation type and
    ``selector`` is adapter cargo. A declaration, not a check binding: the
    session wrapper compiles it and no script or failure policy belongs here
    (ADR-0030).
    """

    declared_by: str
    app: str
    path: tuple[str, ...]
    #: The external operation adapter defines this selector's grammar.
    selector: str


@dataclass(frozen=True)
class ResolvedCheck:
    """One declared row, resolved to an absolute script (HATS-1140, HATS-1545).

    ai-hats owns three of a row's keys — ``run`` (what executes), ``at`` (where)
    and ``on_error`` (how a verdict is read). ``at`` is owned but never
    interpreted: ai-hats only guarantees a row names at least one point, since a
    row bound to nothing is a gate that never fires. ``app`` and ``path`` say
    which application the row was written under and where in that application's
    own tree it sat; ``cargo`` is every remaining key, carried byte-for-byte.
    What a point NAME means, and when it fires, belongs to whoever owns ``app``
    (ADR-0019 D11).

    ``script_path`` is absolute and comes from the declaring skill's
    ``source_path``, never from a provider's tree — that is what makes a row
    fire identically under every surface (ADR-0019 D9).
    """  # comment-length: allow — the ownership split IS the contract

    app: str
    path: tuple[str, ...]
    run: str
    at: tuple[str, ...]
    cargo: Mapping[str, Any]
    on_error: str
    script_path: Path
    declared_by: str
    #: Where ``script_path`` pointed BEFORE a session re-based it onto that
    #: session's frozen mirror; ``None`` when nothing re-based it, which is the
    #: live-resolution mode. Retained so a refusal can tell whether the bytes
    #: that refused are still the bytes the library ships (HATS-1651) — the two
    #: diverge silently as a session ages, and only the rebaser sees both.
    source_path: Path | None = None
    #: The component file that DECLARED this row — distinct from ``source_path``,
    #: which is about the script. What a diagnostic tells the human to open.
    declared_in: Path | None = None

    @property
    def skill(self) -> str:
        """The composed skill ``run`` names — its first segment."""
        return self.run.split("/", 1)[0]

    @property
    def script(self) -> str:
        """The path inside that skill's directory — everything after it."""
        return self.run.split("/", 1)[1] if "/" in self.run else ""


@dataclass(frozen=True)
class CompositionResult:
    """The flattened result of composing a role.

    ``injections`` is the flat deduped view; ``trait_injections`` /
    ``role_injection`` / ``overlay_injection`` carry the same data with
    provenance for layered writers (HATS-282). Frozen (HATS-452): derive
    variants ONLY via the ``with_*`` methods — re-composing the same
    (role, overlays) pair for a variant is forbidden (ADR-0005 D1).
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
    consent: tuple[ConsentPoint, ...] = ()

    @property
    def merged_injection(self) -> str:
        """Concatenate all injections in dependency-tree order."""
        return "\n\n".join(inj for inj in self.injections if inj.strip())

    # ----- immutable transformations (HATS-452) -----

    def with_injection_override(self, text: str) -> "CompositionResult":
        """Return a copy whose ``injections`` is exactly ``[text]``.

        Sub-agent (Automate) path only — HITL has no override channel
        (ADR-0005 D2).
        """
        return replace(self, injections=[text])

    def with_user_rules(self, paths: "Iterable[Path]") -> "CompositionResult":
        """Return a copy carrying the project's user-rule files (HATS-1203).

        Attached by ``compose_for_role`` — the one composition site that knows
        ``project_dir`` — so every consumer of the result inherits them.
        """
        return replace(self, user_rules=tuple(paths))
