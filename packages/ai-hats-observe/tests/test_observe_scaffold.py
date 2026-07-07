"""Scaffold smoke (0.1.0 slice 1) — the package imports and exposes ``__all__``.

Grows into the full standalone/boundary suite as later slices land the domain
modules. Kept as the RED baseline that the package directory + wheel-target +
root testpath/pythonpath wiring are in place.
"""

from __future__ import annotations

import ai_hats_observe


def test_package_imports() -> None:
    assert isinstance(ai_hats_observe.__all__, list)
