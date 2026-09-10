#!/usr/bin/env python3
"""Which lines of a markdown file are prose, and which are inside a code block.

Fences are tracked per CommonMark rather than by toggling on every ```-prefixed
line: a nested block — a ```` fence holding ``` ones — is three toggles instead
of two, and the flipped parity silently un-judges the rest of the file.
"""

from __future__ import annotations

import re

#: A fence line: its run of markers, and whatever follows on the same line.
FENCE_RE = re.compile(r"^[ \t]*(?P<run>(?P<char>[`~])(?P=char){2,})(?P<info>.*)$")


def prose_lines(text: str) -> tuple[list[tuple[int, str]], int | None]:
    """`(lineno, line)` outside code blocks, plus the line of an unclosed fence.

    A sample is not a claim: what a fenced block holds is a command someone
    types, not an assertion about this tree.
    """
    out: list[tuple[int, str]] = []
    opened_at: int | None = None
    fence: tuple[str, int] | None = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        match = FENCE_RE.match(line)
        if match is None:
            if fence is None:
                out.append((lineno, line))
            continue
        char, run, info = match.group("char"), match.group("run"), match.group("info")
        if fence is None:
            # An opening backtick fence may not carry a backtick in its info
            # string — that line is prose holding a code span, not a fence.
            if char == "`" and "`" in info:
                out.append((lineno, line))
                continue
            fence, opened_at = (char, len(run)), lineno
            continue
        if char == fence[0] and len(run) >= fence[1] and not info.strip():
            fence, opened_at = None, None
        # Anything else inside a block is literal content, including a shorter
        # fence of the same character — that is what nesting is made of.

    return out, opened_at
