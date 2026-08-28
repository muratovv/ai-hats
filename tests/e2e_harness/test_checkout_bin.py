"""The e2e tier's surface shims (HATS-1847).

Subject: ``_helpers.surfaces`` — the table naming every executable a PATH entry
must carry for the tier to measure the checkout under test, and the writer that
materialises them.

The trap it closes: a PATH entry holding only ``ai-hats`` left ``rack`` to the
ambient PATH, so ``materialize_consent_wrappers`` recorded ANOTHER checkout's
binary in ``originals`` and ran it — metadata from one tree against code from
another. It stayed invisible until a rename made the skew fatal, because the
interpreter guard watches ``sys.executable`` and ``rack`` never came from it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# The exact filter ``materialize_consent_wrappers`` applies before resolving a
# surface. Imported rather than restated: a second copy would drift, and the
# drift would show up as a green test over the live wrong answer.
from ai_hats.consent_wrapper import _original_lookup_path
from ai_hats_library.hooks.consent_gate.operations import REGISTRY, wrapped_surfaces

from _helpers.surfaces import SURFACES, write_surface_shims


def decoy_bin(root: Path) -> Path:
    """A reachable rival for every surface — the known-present sample.

    Without it, "``rack`` resolved inside the checkout" is also what a machine
    with no ``rack`` anywhere reports, and the assertion proves nothing.
    """
    decoy = root / "decoy"
    decoy.mkdir()
    for surface in wrapped_surfaces(REGISTRY):
        executable = decoy / surface
        executable.write_text("#!/usr/bin/env bash\nexit 0\n")
        executable.chmod(0o755)
    return decoy


def resolve(surface: str, entries: list[Path]) -> Path | None:
    """Where the wrapper would find ``surface`` on a PATH led by ``entries``."""
    path = os.pathsep.join([*(str(e) for e in entries), os.environ.get("PATH", "")])
    found = shutil.which(surface, path=_original_lookup_path(path))
    return Path(found) if found else None


def test_every_wrapped_surface_resolves_inside_the_checkout_bin(tmp_path: Path) -> None:
    bin_dir = write_surface_shims(tmp_path / "bin")
    decoy = decoy_bin(tmp_path)

    for surface in wrapped_surfaces(REGISTRY):
        found = resolve(surface, [bin_dir, decoy])
        assert found is not None, f"{surface} resolved nowhere at all"
        assert found.parent == bin_dir, f"{surface} resolved to {found}, not inside {bin_dir}"


def test_the_decoy_wins_when_the_checkout_bin_is_absent(tmp_path: Path) -> None:
    """Positive control for the test above: the rival really is reachable.

    Drop this and a table that shims nothing still passes on a machine where the
    surface is not installed — the test would be measuring an empty PATH.
    """
    decoy = decoy_bin(tmp_path)

    for surface in wrapped_surfaces(REGISTRY):
        found = resolve(surface, [decoy])
        assert found is not None, f"{surface} resolved nowhere at all"
        assert found.parent == decoy, f"{surface} resolved to {found}, not inside {decoy}"


def test_the_shim_table_covers_every_wrapped_surface() -> None:
    """The registry declares surfaces; the table shims them. Neither reads the other.

    A surface added to ``REGISTRY`` alone is the HATS-1847 hole, reopened: the
    wrapper resolves it from the ambient PATH and nothing says so.
    """
    missing = sorted(set(wrapped_surfaces(REGISTRY)) - set(SURFACES))
    assert not missing, f"consent-wrapped surfaces with no shim: {missing}"
