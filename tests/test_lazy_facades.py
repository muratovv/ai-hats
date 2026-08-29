"""What a PEP 562 facade must keep doing after it stops importing eagerly.

Sibling of ``test_dispatcher_import_closure``: that one holds the cost down, this
one holds the contract still. Both are needed — a facade that exports nothing
would pass the cost gate.

Names only. A submodule reached as an attribute of a bare-imported package is
NOT part of the contract: ``ai_hats_core``'s README scopes the public API to
``__all__``, and the ``ai_hats_observe`` facade declines the same shape.
"""

from __future__ import annotations

import importlib

import pytest

LAZY_FACADES = (
    "ai_hats_core",
    "ai_hats.surfaces",
    "ai_hats.surfaces.agy",
    "ai_hats.surfaces.claude",
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
