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


def refused_only(text: str) -> set[str]:
    """Flags whose ONLY code mention is a deny-list entry: naming one there is the
    opposite of offering a hatch, so there is no bypass to record (HATS-1639).

    A flag named elsewhere too (`AI_HATS_YOLO` — safety_gate both refuses it inline
    and journals it as its own hatch) stays under the ratchet."""
    listed = re.search(r"SELF_GRANT_FORBIDDEN\s*=\s*\(([^)]*)\)", text)
    if not listed:
        return set()
    inside = set(re.findall(r"AI_HATS_[A-Z0-9_]+", listed.group(1)))
    outside = set(re.findall(r"AI_HATS_[A-Z0-9_]+", text.replace(listed.group(0), "")))
    return inside - outside


def hatches_in(text: str) -> set[str]:
    stripped = strip_comments(text)
    return {m.group(0) for m in HATCH_RE.finditer(stripped)} - NOT_A_HATCH - refused_only(stripped)


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
    # HATS-1682: the consent acks are picked from a small table and journaled
    # through the loop variable, so neither the literal nor one constant name
    # appears at the call — the names come from the table instead.
    if re.search(r'journal_bypass\(\s*"hatch"\s*,\s*flag', text):
        journaled |= set(re.findall(r'CONSENT_ACK = "(AI_HATS_[A-Z0-9_]+)"', text))
        journaled |= set(re.findall(r'LEGACY_ACK_BY_TARGET = \{[^}]*"(AI_HATS_[A-Z0-9_]+)"', text))
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


def test_a_flag_only_listed_as_refused_is_not_a_hatch():
    """Refusing a flag is the opposite of offering a bypass (HATS-1639)."""
    text = 'SELF_GRANT_FORBIDDEN = ("AI_HATS_PLAN_ACK", "AI_HATS_MERGE_ACK")\ncode = 1'
    assert hatches_in(text) == set()


def test_a_refused_flag_named_elsewhere_too_stays_under_the_ratchet():
    """The exemption must not launder a real hatch that happens to be deny-listed."""
    text = (
        'SELF_GRANT_FORBIDDEN = ("AI_HATS_YOLO",)\n'
        'if os.environ.get("AI_HATS_YOLO"):\n    sys.exit(0)\n'
    )
    assert hatches_in(text) == {"AI_HATS_YOLO"}
    assert unjournaled(text) == {"AI_HATS_YOLO"}


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


#: The one sanctioned way a gate reaches the journal (HATS-1337). Env first —
#: the dispatcher resolves it and it is the only form correct for a gate shipped
#: outside the builtin library; the relative path is the standalone fallback.
JOURNAL_SOURCE = (
    '. "${AI_HATS_BYPASS_JOURNAL:-$(dirname "$0")/../../../../hooks/bypass_journal.sh}"'
)
JOURNAL_RELATIVE = "../../../../hooks/bypass_journal.sh"


def test_every_gate_sources_the_journal_the_one_sanctioned_way():
    """Gates run in place now, so `$0` is the library path, not a flat copy.

    The pre-1337 `$(dirname "$0")/../bypass_journal.sh` was correct only for the
    retired `.githooks/<event>.d/` copy. Getting this wrong degrades into the
    "NOT RECORDED" stub — silently, and only at the moment somebody uses a
    hatch, which is exactly when the trail is needed.
    """
    sourcing = [
        p for p in hook_files() if "bypass_journal.sh" in p.read_text() and p.parent.name != "hooks"
    ]
    assert len(sourcing) >= 8, (
        f"discovery went stale — only {len(sourcing)} gates source the journal"
    )

    wrong_form = [str(p.relative_to(LIB)) for p in sourcing if JOURNAL_SOURCE not in p.read_text()]
    assert not wrong_form, f"must source the journal as `{JOURNAL_SOURCE}`: {wrong_form}"

    unresolvable = [
        str(p.relative_to(LIB))
        for p in sourcing
        if not (p.parent / JOURNAL_RELATIVE).resolve().is_file()
    ]
    assert not unresolvable, (
        f"the standalone fallback does not resolve from these gates: {unresolvable}"
    )


def test_the_documented_gaps_are_still_gaps():
    """A wired entry must leave the list, so the ratchet cannot go stale."""
    corpus = "\n".join(p.read_text() for p in hook_files())
    stale = {h for h in KNOWN_UNJOURNALED if h not in unjournaled(corpus)}
    assert not stale, f"now journaled — drop from KNOWN_UNJOURNALED: {sorted(stale)}"
