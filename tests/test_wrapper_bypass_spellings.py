"""HATS-1781 — the D6 boundary must know every spelling the shared table knows.

ADR-0030 D6: the runtime boundary refuses an alternate spelling of a protected
operation, because a `PATH` shim alone is not a security boundary. HATS-1754 put
every spelling in ONE table so the guard and the allow-rule lint cannot drift —
and its docstring says why: "a spelling the guard watches while the lint stays
silent is the hole the lint exists to report".

Measured 2026-08-22 on `4134d9dc`, the drift ran the other way: of the fifteen
spellings the table generates for a guarded transition, EIGHT walked past the
boundary — every `-m ai_hats_rack.cli` form and the `uvx` runner — because the
boundary matched the module name against a literal and never looked at runners.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard" / "hooks"
)

#: Spellings the table generates that no shell can actually run, so the boundary
#: owes them nothing. `uv` has no passthrough subcommand — measured 2026-08-22:
#: `uv rack --help` answers "unrecognized subcommand 'rack'". Named here rather
#: than filtered silently: the table over-generating is its own small defect.
_NOT_EXECUTABLE = ("uv rack ", "uv ai-hats ")

#: What the role declares, handed to the boundary instead of patched into it.
_DECLARED = lambda: frozenset({"done", "execute"})  # noqa: E731


def _load(name: str):
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard():
    sys.path.insert(0, str(HOOKS))
    try:
        _load("consent_spellings")
        yield _load("safety_gate")
    finally:
        sys.path.remove(str(HOOKS))


@pytest.fixture(scope="module")
def spellings():
    return _load("consent_spellings")


def _guarded_spellings(spellings_mod, command: str) -> list[str]:
    out = []
    for text in spellings_mod.spellings_for(command):
        if text == command or text.startswith(_NOT_EXECUTABLE):
            continue  # the canonical call is the wrapper's own; the rest cannot run
        out.append(text)
    return out


def test_the_table_and_the_boundary_agree_on_a_guarded_transition(guard, spellings):
    """Every executable spelling of a declared transition is refused as a bypass."""
    missed = [
        text
        for text in _guarded_spellings(spellings, "rack transition HATS-1 done")
        if not guard._wrapper_bypass_verdict(text, targets=_DECLARED)
    ]

    assert not missed, "these spellings reach the tool without the wrapper:\n" + "\n".join(missed)


def test_the_canonical_call_is_not_reported_as_a_bypass(guard):
    """The discriminator: a boundary refusing everything would pass the test above
    while making the wrapped command itself unrunnable."""
    assert not guard._wrapper_bypass_verdict("rack transition HATS-1 done", targets=_DECLARED)


def test_an_undeclared_target_is_no_bypass_whatever_the_spelling(guard, spellings):
    """The other discriminator: the boundary judges DECLARED operations, so a role
    that declares nothing must not have its module spellings refused."""
    refused = [
        text
        for text in _guarded_spellings(spellings, "rack transition HATS-1 done")
        if guard._wrapper_bypass_verdict(text, targets=frozenset)
    ]

    assert not refused, "an undeclared operation was refused:\n" + "\n".join(refused)
