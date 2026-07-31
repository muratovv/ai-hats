"""A gate nobody tested is a gate nobody has checked.

HATS-1372. The epic audit found 15 of 21 defects reached production because the
detection layers were themselves broken. This is the ratchet: a gate script must
be exercised by some test, or be listed below with the reason it is not. The
list may shrink, never grow — adding a gate without a test fails here.

It proves *named by a test*, not *RED*: no static check can tell an assertion
that fires from one that cannot, and a gate can be named by a test that covers
only its no-op path — `pre-commit-smoke.sh` is exactly that today. The RED
requirement belongs to `dev_rule_e2e_gate` §4 at review time. What this catches
is the cruder failure, a gate no test touches at all, which is how
`wt_entry_gate.py` shipped and stayed unnoticed through a full audit.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

GATE_GLOBS = (
    "packages/**/git_hooks/*.sh",
    "packages/**/hooks/*.py",
    "packages/**/hooks/*.sh",
    "scripts/check_*.py",
)

TEST_ROOTS = ("tests", "packages")

#: Gate -> why no test names it. Remove an entry when you add the test.
KNOWN_UNCOVERED: dict[str, str] = {}


def discover_gates(root: Path) -> set[str]:
    """Basenames of every gate script, found by directory convention."""
    return {p.name for glob in GATE_GLOBS for p in root.glob(glob)}


def mentioned_in_tests(root: Path) -> set[str]:
    """Every token that appears anywhere in a test file, as a coarse coverage proxy."""
    blobs = []
    for test_root in TEST_ROOTS:
        for path in (root / test_root).rglob("test_*.py"):
            blobs.append(path.read_text(errors="ignore"))
    return set("\n".join(blobs).split())


def uncovered(gates: set[str], corpus: set[str], known: dict[str, str]) -> set[str]:
    """Gates that no test names and that are not on the documented list."""
    named = {g for g in gates if any(g in token for token in corpus)}
    return gates - named - set(known)


def test_a_gate_no_test_names_is_reported():
    assert uncovered({"new_gate.sh"}, {"other_gate.sh"}, {}) == {"new_gate.sh"}


def test_a_gate_a_test_names_is_not_reported():
    assert uncovered({"new_gate.sh"}, {'HOOK="hooks/new_gate.sh"'}, {}) == set()


def test_a_documented_gap_is_not_reported():
    assert uncovered({"old.sh"}, set(), {"old.sh": "reason"}) == set()


def test_every_gate_is_exercised_or_documented():
    gates = discover_gates(REPO_ROOT)
    assert gates, "gate discovery found nothing — the globs went stale"

    missing = uncovered(gates, mentioned_in_tests(REPO_ROOT), KNOWN_UNCOVERED)

    assert not missing, (
        "these gate scripts are named by no test — add one that proves the gate "
        f"fires, or document the gap in KNOWN_UNCOVERED: {sorted(missing)}"
    )


def test_the_documented_gaps_are_still_gaps():
    """A covered entry must leave the list, so the ratchet cannot go stale."""
    corpus = mentioned_in_tests(REPO_ROOT)
    stale = {g for g in KNOWN_UNCOVERED if any(g in token for token in corpus)}

    assert not stale, f"now exercised by tests — drop from KNOWN_UNCOVERED: {sorted(stale)}"
