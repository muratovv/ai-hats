"""`rack:` is the framework's own space, and only the framework can spell it.

A script reading ``actor`` off the call envelope (HATS-1724) decides "is this
automation" by the prefix, which is worth writing only if (A) every actor the
framework mints for itself lives under ``rack:`` and (B) the CLI road cannot
mint that prefix. Without A the predicate misses an automation; without B a
caller could claim to be one. The literals are found by AST rather than listed,
so a fifth automation actor cannot be added quietly outside the space.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOTS = (REPO_ROOT / "src" / "ai_hats", REPO_ROOT / "packages" / "ai-hats-rack" / "src")

#: The reserved space itself. Bare `rack` would also match `rack_workspace`.
NAMESPACE = "rack:"


def _actor_literals() -> dict[str, str]:
    """Module-level ``*_ACTOR = "…"`` constants across the integrator and the rack."""
    found: dict[str, str] = {}
    for root in SOURCE_ROOTS:
        for module in sorted(root.rglob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                if not isinstance(node.value, ast.Constant) or not isinstance(
                    node.value.value, str
                ):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.endswith("_ACTOR"):
                        found[f"{module.name}:{target.id}"] = node.value.value
    return found


def test_every_actor_the_framework_mints_for_itself_lives_under_rack():
    literals = _actor_literals()

    assert literals, "no *_ACTOR constant found — the finder stopped seeing them"
    off_space = {name: value for name, value in literals.items() if not value.startswith(NAMESPACE)}
    assert not off_space, (
        f"these automation actors sit outside {NAMESPACE!r}: {off_space}. A gate "
        f"asking 'is this automation' by prefix would read them as a human move."
    )


@pytest.mark.parametrize(
    ("session_id", "expected_prefix"),
    [("20260820-130749-1-96055", "session:"), ("", "human:")],
)
def test_the_cli_road_cannot_mint_the_reserved_prefix(monkeypatch, session_id, expected_prefix):
    """Both branches of the one function that answers 'who is moving this card'
    on the CLI road — with a session, and at a bare terminal."""
    from ai_hats_observe.trace import ENV_SESSION_ID
    from ai_hats_rack.cli_common import actor

    monkeypatch.setenv(ENV_SESSION_ID, session_id)

    minted = actor()

    assert minted.startswith(expected_prefix)
    assert not minted.startswith(NAMESPACE)
