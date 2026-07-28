"""The id grammar — one leaf module, shared by the router and the write gate.

``workspace`` imports ``kernel``, so a grammar both need cannot live in either
(HATS-1283). The tail after ``<prefix>-<digit>`` is the author's business:
``HATS-621S`` and ``HATS-1247-fix`` route exactly like ``HATS-1249``.
"""

from __future__ import annotations

import re

#: An ``<id>`` opens with ``<prefix>-<digit>``; the SHORTEST such run is the
#: prefix, so a hyphenated prefix survives (``MY-PROJ-42`` -> ``MY-PROJ``).
_ID_RE = re.compile(r"^(?P<prefix>.+?)-\d")


def prefix_of(item_id: str) -> str | None:
    """The routing prefix of an id, or ``None`` when it has no ``-<digit>`` split
    to make — "unparseable" is its own answer, never the whole id posing as a
    prefix (which read back as ``no backlog for prefix 'HATS-fix'``)."""
    match = _ID_RE.match(item_id)
    return match.group("prefix") if match else None


__all__ = ["prefix_of"]
