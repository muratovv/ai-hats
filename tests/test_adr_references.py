"""An ADR points at a symbol, never at a line number (HATS-1655).

A line number rots from any edit ABOVE it, in a file the citing author never
touches — so the reference decays with nobody doing anything wrong. ADR-0026
stated its `file:line` refs had been re-verified at a named commit; 13 of 20
were stale by the time anyone looked, and the worst had drifted onto unrelated
code, which misleads a reader rather than merely losing them.

Why this test and not a checker that VALIDATES the numbers: a checker can only
confirm the file exists and is long enough, and both were true of every stale
ref. It would have been green in exactly the case worth catching. Removing the
form is the only fix that holds — so the form is what this refuses.

The convention itself lives in CONTRIBUTING.md, "Never cite a line number".
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = REPO_ROOT / "docs" / "adr"

#: `path/to/file.ext:12` or `:12-30`, the two spellings the ADRs actually used.
#: The extension list is closed on purpose: a bare `word:12` matches prose like
#: "budget:20" and markdown anchors, and a checker that cries wolf gets muted.
_LINE_REF = re.compile(
    r"\b[\w./-]+\.(?:py|sh|md|yaml|yml|toml|json|cfg|ini|txt|d2):\d+(?:-\d+)?\b"
)

#: Fenced code is exempt: a shell transcript or a compiler diagnostic quoted
#: verbatim is evidence, not a citation the reader is meant to follow.
_FENCE = re.compile(r"^\s*(?:```|~~~)")


def _citations(text: str) -> list[tuple[int, str]]:
    """Line-number citations in prose, skipping fenced blocks."""
    hits: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        hits.extend((lineno, match.group(0)) for match in _LINE_REF.finditer(line))
    return hits


def test_no_adr_cites_a_line_number():
    """The ratchet. Its failure message is also the worklist."""
    offenders: dict[str, list[tuple[int, str]]] = {}
    for adr in sorted(ADR_DIR.glob("*.md")):
        found = _citations(adr.read_text(encoding="utf-8"))
        if found:
            offenders[adr.name] = found

    total = sum(len(v) for v in offenders.values())
    assert not offenders, (
        f"{total} line-number citation(s) in {len(offenders)} ADR(s). A line number "
        f"rots from any edit above it — name the file and the symbol instead "
        f"(CONTRIBUTING.md, 'Never cite a line number'):\n"
        + "\n".join(
            f"  {name}: " + ", ".join(f"L{ln} {ref}" for ln, ref in refs)
            for name, refs in sorted(offenders.items())
        )
    )


def test_the_matcher_reads_a_citation_and_spares_a_shell_transcript():
    """Guards the guard: too loose and it gets muted, too tight and it is decor."""
    assert _citations("see `checks.py:29` for the priority") == [(1, "checks.py:29")]
    assert _citations("the range `checks.py:284-303`") == [(1, "checks.py:284-303")]
    # A symbol reference — the form this test exists to push authors toward.
    assert _citations("`CheckSubscriber.__init__` (`checks.py`)") == []
    # Prose that merely contains a colon and digits.
    assert _citations("the budget is 20s and the lock:30 is wider") == []
    assert _citations("```\n$ ruff check foo.py:1:1\n```") == []
