"""Contract guard: CompositionResult is immutable (HATS-452 / П1).

ADR-0005 П1 — composition is an immutable first-class object: fields
cannot be reassigned after construction; modifications go through
explicit ``with_*`` methods that return new instances. This file locks
the contract at the type level so a refactor cannot silently regress
back to a mutable surface that the rest of the codebase can monkey with
in inconsistent places.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ai_hats.composer import CompositionResult, ResolvedComponent
from ai_hats.models import ComponentType, HooksConfig


def _make_minimal_result(
    injections: list[str] | None = None,
) -> CompositionResult:
    return CompositionResult(
        name="test",
        priorities=["Reliability"],
        rules=[],
        skills=[],
        hooks=HooksConfig(),
        injections=injections if injections is not None else ["body"],
    )


def test_composition_result_is_frozen():
    """Field reassignment on CompositionResult raises FrozenInstanceError."""
    r = _make_minimal_result()
    with pytest.raises(FrozenInstanceError):
        r.injections = ["mutated"]  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        r.name = "other"  # type: ignore[misc]


def test_resolved_component_is_frozen():
    """ResolvedComponent is also frozen — composer outputs are immutable
    end-to-end (П1 applies to the components too)."""
    c = ResolvedComponent(
        name="rule_x",
        component_type=ComponentType.RULE,
        source_path=Path("/tmp/rule_x"),
        injection="body",
    )
    with pytest.raises(FrozenInstanceError):
        c.injection = "tampered"  # type: ignore[misc]


def test_with_injection_override_returns_new_instance():
    """``with_injection_override`` returns a new CompositionResult; the
    original's injections list is unchanged. Locks the П1 method API."""
    original = _make_minimal_result(injections=["one", "two"])
    derived = original.with_injection_override("override text")

    assert derived is not original, "with_* must return a new instance"
    assert derived.injections == ["override text"]
    # Original is intact — frozen + replace gives a fresh list, not a
    # mutation of the original's list.
    assert original.injections == ["one", "two"]
    # Other fields carry over.
    assert derived.name == original.name
    assert derived.priorities == original.priorities


def test_with_injection_override_with_empty_string_is_explicit_clear():
    """Passing ``""`` produces a result whose ``merged_injection`` is
    empty — the consumer-side filter (in providers.build_system_prompt)
    will then emit no injection section. This is the documented edge
    case (empty string is valid; absent==None is the trap)."""
    r = _make_minimal_result()
    cleared = r.with_injection_override("")
    assert cleared.injections == [""]
    assert cleared.merged_injection == ""
