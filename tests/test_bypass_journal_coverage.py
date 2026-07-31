"""HATS-1407 — a hatch nobody journals is a bypass nobody can see.

The ratchet for the journal, mirroring `test_gate_red_coverage.py`: every gate
hatch in every hook file must sit in a branch that records the bypass. The
KNOWN_UNJOURNALED list may shrink, never grow — adding a hatch without a
journal call fails here.

It proves *the file journals that variable*, not *the branch is reachable* — no
static check can tell a live branch from a dead one. What it catches is the
cruder failure, a hatch wired to nothing, which is how the whole class started.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"

HOOK_GLOBS = (
    "core/skills/*/git_hooks/*.sh",
    "usage/skills/*/git_hooks/*.sh",
    "core/skills/*/hooks/*.sh",
    "core/skills/*/hooks/*.py",
    "usage/skills/*/hooks/*.sh",
    "usage/skills/*/hooks/*.py",
    "hooks/*.sh",
)

#: Env vars that read as hatches but are not: they configure a gate rather than
#: disable it, so there is no bypass to record.
NOT_A_HATCH = {
    "AI_HATS_SKILL_LINT_CMD",
    "AI_HATS_RULE_DELIVERY_CMD",
    "AI_HATS_WT_GATE_EXTS",
}

#: hatch -> why it is not journaled yet. Remove an entry when you wire the call.
KNOWN_UNJOURNALED: dict[str, str] = {}

HATCH_RE = re.compile(r"AI_HATS_[A-Z0-9_]*(?:_ACK|_SKIP|_OFF|YOLO)\b")


def hook_files() -> list[Path]:
    return sorted(p for glob in HOOK_GLOBS for p in LIB.glob(glob))


def strip_comments(text: str) -> str:
    """Drop `#` comment bodies — every hook documents its hatch in prose, and a
    hatch OWNED ELSEWHERE gets named there too (safety_gate.py cites the rack
    consent gates). Scanning prose would demand a journal call for both."""
    out = []
    for line in text.splitlines():
        head, sep, _ = line.partition("#")
        # A `#` inside quotes is not a comment; keep such lines whole.
        out.append(line if sep and (head.count('"') % 2 or head.count("'") % 2) else head)
    return "\n".join(out)


def hatches_in(text: str) -> set[str]:
    return {m.group(0) for m in HATCH_RE.finditer(strip_comments(text))} - NOT_A_HATCH


def unjournaled(text: str) -> set[str]:
    """Hatches the file names but never passes to a journal call."""
    journaled = set(
        re.findall(r"(?:ai_hats_)?journal_bypass\W+\w+\W+\"?(AI_HATS_[A-Z0-9_]+)", text)
    )
    # The Python hooks pass the module constant, not the literal name.
    if re.search(r"journal_bypass\(\s*\"hatch\"\s*,\s*_KILL_SWITCH", text):
        journaled |= set(re.findall(r'_KILL_SWITCH\s*=\s*"(AI_HATS_[A-Z0-9_]+)"', text))
    if re.search(r"journal_bypass\(\s*\"hatch\"\s*,\s*DESTRUCTIVE_ACK", text):
        journaled |= set(re.findall(r'DESTRUCTIVE_ACK\s*=\s*"(AI_HATS_[A-Z0-9_]+)"', text))
    return hatches_in(text) - journaled


def test_discovery_finds_the_hooks():
    """Green must mean 'checked and clean', never 'matched nothing'."""
    files = hook_files()
    assert len(files) >= 10, [str(p.relative_to(LIB)) for p in files]


def test_a_hatch_with_no_journal_call_is_reported():
    assert unjournaled('if [ "$AI_HATS_FOO_ACK" ]; then exit 0; fi') == {"AI_HATS_FOO_ACK"}


def test_a_journaled_hatch_is_not_reported():
    text = 'if [ "$AI_HATS_FOO_ACK" ]; then\n ai_hats_journal_bypass hatch AI_HATS_FOO_ACK\nfi'
    assert unjournaled(text) == set()


def test_a_hatch_named_only_in_a_comment_is_not_reported():
    """safety_gate.py cites the rack consent gates it mirrors; it does not own them."""
    assert unjournaled("# Mirrors AI_HATS_PLAN_ACK / AI_HATS_MERGE_ACK\ncode = 1") == set()


def test_a_configuring_var_is_not_treated_as_a_hatch():
    assert hatches_in('_cmd="${AI_HATS_SKILL_LINT_CMD:-npx agnix}"') == set()


def test_every_hatch_in_every_hook_is_journaled():
    offenders: dict[str, list[str]] = {}
    for path in hook_files():
        missing = unjournaled(path.read_text()) - set(KNOWN_UNJOURNALED)
        if missing:
            offenders[str(path.relative_to(LIB))] = sorted(missing)

    assert not offenders, (
        "these hatches skip a gate without recording it — add the "
        f"journal_bypass call, or document the gap in KNOWN_UNJOURNALED: {offenders}"
    )


def test_the_documented_gaps_are_still_gaps():
    """A wired entry must leave the list, so the ratchet cannot go stale."""
    corpus = "\n".join(p.read_text() for p in hook_files())
    stale = {h for h in KNOWN_UNJOURNALED if h not in unjournaled(corpus)}
    assert not stale, f"now journaled — drop from KNOWN_UNJOURNALED: {sorted(stale)}"
