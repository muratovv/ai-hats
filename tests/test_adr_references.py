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

#: `path/to/file.ext:12` and `path/to/file.ext:12-30` — a named file plus lines.
#: The extension list is closed on purpose: an open `word:12` matches prose like
#: "budget:20" and markdown anchors, and a checker that cries wolf gets muted.
_NAMED_REF = re.compile(
    r"\b[\w./-]+\.(?:py|sh|md|yaml|yml|toml|json|cfg|ini|txt|d2):\d+(?:-\d+)?\b"
)

#: Inside backticks, ANY token carrying `:<digits>` is a citation.
#:
#: This one is broad on purpose, and the breadth was earned. Enumerating the
#: shapes failed three times in a single sweep (HATS-1655): first `` `:182-191` ``
#: with the filename left to a table heading, then `scripts/ai-hats-launcher:18`
#: with no extension, then `` `0014-composable-component-decomposition:361-377` ``
#: — no extension, no slash, and the backtick not adjacent to the colon, so it
#: slipped all three narrow patterns. Every one was found by someone converting
#: refs by hand, never by re-reading the regex. So the default is inverted here:
#: inside backticks, colon-plus-digits is guilty, and a new exception has to be
#: argued for rather than silently unmatched. Measured at zero false positives
#: across all 26 ADRs.
#:
#: Backticks are what make the breadth safe — bare prose carries ratios ("1:3"),
#: times ("09:30") and ports, and matching those is the cry-wolf that gets a
#: guard muted.
_BACKTICKED_REF = re.compile(r"`[^`\s]*:\d+(?:-\d+)?[^`\s]*`")

#: `scripts/ai-hats-launcher:18` — a real path whose file has no extension, so
#: the closed list above cannot see it. ADR-0025 carried eleven (HATS-1655).
#: Requires a slash, so it is a path and not prose; the lookbehind keeps
#: `https://host:8080` out. Matches with a known extension are dropped below so
#: one citation is not reported twice.
_UNSUFFIXED_REF = re.compile(r"(?<!/)\b[\w.-]+/[\w./-]+:\d+(?:-\d+)?\b")

#: Backticks first: it is the broad one, so it claims the span and the narrow
#: two only report what falls outside any backticks.
_PATTERNS = (_BACKTICKED_REF, _NAMED_REF, _UNSUFFIXED_REF)

#: Fenced code is exempt: a shell transcript or a compiler diagnostic quoted
#: verbatim is evidence, not a citation the reader is meant to follow.
_FENCE = re.compile(r"^\s*(?:```|~~~)")


def _citations(text: str) -> list[tuple[int, str]]:
    """Line-number citations in prose, skipping fenced blocks.

    Deduped by OVERLAP, not by endpoint: the patterns nest — the backticked one
    swallows the quotes the other two stop short of — so one citation would
    otherwise be reported two or three times and inflate the worklist it exists
    to be. Widest first, so the outer span claims the region.
    """
    hits: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        claimed: list[tuple[int, int]] = []
        for pattern in _PATTERNS:
            for match in pattern.finditer(line):
                start, end = match.span()
                if any(start < c_end and c_start < end for c_start, c_end in claimed):
                    continue
                claimed.append((start, end))
                hits.append((lineno, match.group(0)))
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
    assert _citations("see `checks.py:29` for the priority") == [(1, "`checks.py:29`")]
    assert _citations("the range `checks.py:284-303`") == [(1, "`checks.py:284-303`")]
    # Unbackticked prose still gets caught — that is what the narrow two are for.
    assert _citations("see checks.py:29 for the priority") == [(1, "checks.py:29")]
    # A symbol reference — the form this test exists to push authors toward.
    assert _citations("`CheckSubscriber.__init__` (`checks.py`)") == []
    # Prose that merely contains a colon and digits.
    assert _citations("the budget is 20s and the lock:30 is wider") == []
    assert _citations("```\n$ ruff check foo.py:1:1\n```") == []
    # The filename-less form: lines scoped by a table heading somewhere above.
    # It hid from the first version of this guard, which is why it is pinned.
    assert _citations("| `_write_prompt_file` | `:182-191` |") == [(1, "`:182-191`")]
    assert _citations("the row cites `:106`") == [(1, "`:106`")]
    # ...and what it must NOT swallow: a ratio, a time, a port in prose.
    assert _citations("a 1:3 ratio at 09:30 on port 8080") == []
    # A path whose file carries no extension — the closed list above is blind to
    # it, so a third pattern covers it. ADR-0025 carried eleven.
    assert _citations("scripts/ai-hats-launcher:18 sets it") == [(1, "scripts/ai-hats-launcher:18")]
    assert _citations("fetch https://example.com:8080/x") == []
    # One citation, two patterns, one report — the dedupe.
    assert _citations("see `src/ai_hats/checks.py:29`") == [(1, "`src/ai_hats/checks.py:29`")]
    # The shape that slipped all three narrow patterns: no extension, no slash,
    # and the backtick not adjacent to the colon. The broad backticked rule is
    # what catches it, and catching THIS is why the rule is broad.
    assert _citations("см. (`0014-composable-component-decomposition:361-377`)") == [
        (1, "`0014-composable-component-decomposition:361-377`")
    ]
