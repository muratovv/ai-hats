"""A surface reaching past ``dispatch`` is the fifth hand-written copy (HATS-1868).

The card's whole claim is that the flow exists once. Nothing in the type system
holds that: a channel can import ``run_chain`` and rebuild the skeleton beside
the one it was given, and every other test would stay green — which is how the
reduction came to live in three different places AFTER HATS-1858 unified it.

So the claim is checked as syntax. This is a RATCHET, not a verdict on the four
surfaces that predate the interface: they are listed as still hand-written, and
HATS-1871 empties that list one surface at a time.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SURFACES = Path(__file__).resolve().parents[1] / "src" / "ai_hats" / "surfaces"

#: Reaching for any of these is rebuilding the flow: they are the steps
#: ``dispatch`` takes, and a channel is meant to be handed their result.
THE_FLOW = frozenset({"run_chain", "reduce_to", "undeliverable", "relay_stderr"})

#: Surfaces whose dispatcher predates the interface. HATS-1871 moves them over
#: and deletes their line; a NEW name here needs the same review as a new copy.
STILL_HAND_WRITTEN = frozenset({"agy", "cline", "codex", "opencode"})


def _called_names(path: Path) -> set[str]:
    """Every plain function name this module CALLS.

    Calls, not imports: re-exporting a name is not rebuilding the flow, and a
    test that could not tell the two apart would refuse the honest case.
    """
    called: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    return called


def _surface_modules(surface: str) -> list[Path]:
    return [
        path for path in sorted((SURFACES / surface).rglob("*.py")) if "tests" not in path.parts
    ]


def _reaching(surface: str) -> dict[str, set[str]]:
    return {
        path.name: hit
        for path in _surface_modules(surface)
        for hit in [_called_names(path) & THE_FLOW]
        if hit
    }


def test_the_claude_channel_reaches_the_chain_only_through_dispatch() -> None:
    reaching = _reaching("claude")
    assert reaching == {}, (
        f"claude rebuilds the flow instead of being handed it: {reaching}. "
        f"The five answers are the surface's; the steps between them are dispatch's."
    )


@pytest.mark.parametrize("surface", sorted(STILL_HAND_WRITTEN))
def test_the_positive_control_the_walk_finds_a_hand_written_flow(surface: str) -> None:
    """Without this, the green above is indistinguishable from a walk that
    parses nothing — and the four dispatchers are the known-present sample."""
    assert _reaching(surface), (
        f"{surface} no longer reaches the flow by hand. If HATS-1871 moved it onto "
        f"dispatch, drop it from STILL_HAND_WRITTEN — the ratchet only tightens."
    )


def test_the_walk_reads_calls_rather_than_imports(tmp_path: Path) -> None:
    """The negative control that keeps the rule honest: a module may name these
    to re-export them, and calling that a violation would refuse the facade."""
    module = tmp_path / "reexport.py"
    module.write_text("from x import run_chain\n__all__ = ['run_chain']\n", encoding="utf-8")
    assert _called_names(module) & THE_FLOW == set()
