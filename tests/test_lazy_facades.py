"""What a PEP 562 facade must keep doing after it stops importing eagerly.

Sibling of ``test_dispatcher_import_closure``: that one holds the cost down, this
one holds the contract still. Both are needed — a facade that exports nothing
would pass the cost gate.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys

import pytest

LAZY_FACADES = (
    "ai_hats_core",
    "ai_hats.surfaces",
    "ai_hats.surfaces.agy",
    "ai_hats.surfaces.cline",
    "ai_hats.surfaces.codex",
    "ai_hats.surfaces.opencode",
)


@pytest.mark.parametrize("facade", LAZY_FACADES)
def test_every_exported_name_resolves(facade: str) -> None:
    module = importlib.import_module(facade)
    assert module.__all__, f"{facade} exports nothing"
    for name in module.__all__:
        assert getattr(module, name) is not None


@pytest.mark.parametrize("facade", LAZY_FACADES)
def test_unknown_name_raises_attribute_error(facade: str) -> None:
    """The submodule fallback of ``from pkg import x`` depends on this."""
    module = importlib.import_module(facade)
    with pytest.raises(AttributeError):
        module.definitely_not_exported


@pytest.mark.parametrize("facade", LAZY_FACADES)
def test_dir_lists_untouched_names(facade: str) -> None:
    """Fresh process: in this one the names may already be bound by another test."""
    probe = f"import json, {facade} as m; print(json.dumps(sorted(set(m.__all__) - set(dir(m)))))"
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [], f"{facade}: dir() hides exported names"


def test_submodule_access_survives_the_lazy_getattr() -> None:
    from ai_hats.surfaces import profiles

    assert profiles.__name__ == "ai_hats.surfaces.profiles"


def test_deprecated_provider_aliases_still_resolve() -> None:
    """HATS-1826 kept these for out-of-tree surfaces; nothing else covers them."""
    from ai_hats.surfaces import (
        Provider,
        ProviderHint,
        ProviderRunResult,
        Surface,
        SurfaceHint,
        SurfaceRunResult,
    )

    assert (Provider, ProviderHint, ProviderRunResult) == (
        Surface,
        SurfaceHint,
        SurfaceRunResult,
    )
