"""The hook dispatcher's import closure stays light — a fresh process per tool call.

Three independent edges reach the same heavy subgraph and each is sufficient
ALONE, so the gate is a deny-list per module: a timing threshold would flake and
would not name the edge that grew back. Real subprocess because pytest's own
collection has already polluted ``sys.modules`` here.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

# Every entry is reachable from at least one of the three cut edges, so a
# regression on any single edge fails this gate rather than hiding behind the
# other two.
HEAVY = (
    "pydantic",  # ai_hats_core.yaml_model — the biggest single item
    "pydantic_core",
    "filelock",  # ai_hats_core.locks
    "asyncio",  # pulled in turn by filelock
    "yaml",  # ai_hats.config, under surfaces.contract
    "ai_hats.models",  # the schema layer, under surfaces.contract
)

DISPATCHERS = tuple(
    f"ai_hats.surfaces.{surface}.hook_dispatcher"
    for surface in ("agy", "cline", "codex", "opencode")
)


def _import_closure(module: str) -> frozenset[str]:
    """Names in ``sys.modules`` after importing ``module`` in a fresh process."""
    probe = f"import json, sys; import {module}; print(json.dumps(sorted(sys.modules)))"
    done = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, f"import {module} failed:\n{done.stderr}"
    return frozenset(json.loads(done.stdout))


def _heavy_in(closure: frozenset[str], forbidden: tuple[str, ...]) -> list[str]:
    return sorted(
        name for name in closure if any(name == f or name.startswith(f"{f}.") for f in forbidden)
    )


@pytest.mark.parametrize("dispatcher", DISPATCHERS)
def test_dispatcher_closure_excludes_heavy_imports(dispatcher: str) -> None:
    found = _heavy_in(_import_closure(dispatcher), HEAVY)
    assert not found, (
        f"{dispatcher} imports {found} — an eager import grew back onto the hook "
        f"path. Each of the three lazy facades (ai_hats_core/__init__, "
        f"ai_hats/surfaces/__init__, ai_hats/surfaces/<surface>/__init__) is "
        f"individually sufficient to cause this; find which one you made eager."
    )


def test_deadline_does_not_drag_the_core_facade() -> None:
    """``Deadline`` is pure stdlib — importing it must not load core's deps.

    ``hook_channel`` and ``hook_exec`` both reach it, so this is the edge that a
    move of ``Deadline`` alone would not have closed.
    """
    found = _heavy_in(
        _import_closure("ai_hats_core.deadline"),
        ("pydantic", "pydantic_core", "filelock", "asyncio"),
    )
    assert not found, f"ai_hats_core.deadline drags {found} through ai_hats_core/__init__"


def test_hook_channel_does_not_drag_the_surfaces_facade() -> None:
    """The channel needs neither the surface contract nor the schema layer."""
    found = _heavy_in(
        _import_closure("ai_hats.surfaces.hook_channel"),
        ("ai_hats.models", "ai_hats.resolver", "yaml"),  # pydantic would fire on core too
    )
    assert not found, f"ai_hats.surfaces.hook_channel drags {found} via surfaces/__init__"
