"""Read the observable that ``ai-hats wait`` emits on success (HATS-1493).

``wait`` prints ``happened at <ts> after <elapsed>s (<n> polls)``. Asserting on
it is what separates "waited for the event" from "returned immediately" — an
exit code alone cannot, so tests that only check exit 0 pass against a ``wait``
with its polling loop deleted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HAPPENED = re.compile(r"happened at \S+ after ([0-9.]+)s \((\d+) polls?\)")


@dataclass(frozen=True)
class Happened:
    elapsed_s: float
    polls: int


def parse_happened(stdout: str) -> Happened:
    """Parse the success line, or fail loudly naming what was printed instead."""
    match = _HAPPENED.search(stdout)
    if match is None:
        raise AssertionError(
            f"stdout carries no 'happened at ... (N polls)' line — got: {stdout!r}"
        )
    return Happened(elapsed_s=float(match.group(1)), polls=int(match.group(2)))
