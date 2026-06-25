"""Guard-test: the behind-upstream signal has ONE reader (HATS-846).

The bug this closes was structural — ``CacheEntry.has_update`` had two readers
(the banner and hook self-heal) that hand-copied *different* subsets of the
guards ``{is_local_channel, sha_matches, is_disabled}``, and one diverged. The
fix routes every consumer through the canonical ``upstream_update`` predicate.

This test fails the moment a new consumer reads ``.has_update`` directly instead
of calling ``upstream_update`` — so the guard set can never silently diverge
per-consumer again. If you are adding a legitimate new reader, you are almost
certainly meant to call ``update_check.upstream_update`` instead.
"""

from __future__ import annotations

from pathlib import Path

import ai_hats

# The single allowed reader: the canonical predicate's own `not entry.has_update`.
_CANONICAL_READER = "update_check/__init__.py"


def _real_has_update_readers() -> set[str]:
    """Files under the package with a *code* read of ``.has_update``.

    Docstring/prose mentions wrap the name in rst backticks
    (``CacheEntry.has_update``); a real attribute access does not — so a line
    containing a backtick is treated as prose, not a reader.
    """
    pkg_root = Path(ai_hats.__file__).parent
    readers: set[str] = set()
    for path in pkg_root.rglob("*.py"):
        for line in path.read_text().splitlines():
            if ".has_update" in line and "`" not in line:
                readers.add(path.relative_to(pkg_root).as_posix())
                break
    return readers


def test_has_update_has_a_single_canonical_reader():
    assert _real_has_update_readers() == {_CANONICAL_READER}
