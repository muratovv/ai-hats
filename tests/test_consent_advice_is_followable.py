"""A consent gate must print a recipe that works when followed literally.

`safety_gate.check_self_grant` denies an inline `AI_HATS_*_ACK=1 <cmd>` prefix, so
a recipe spelled that way sends the agent at a wall (HATS-1639; same shape as
HATS-1294 / HATS-1630). Consent is the supervisor's: the recipe must say `export`.

Saying `export` is necessary, not sufficient (HATS-1654): alone on its line it
dies with the shell that ran it, so a recipe read one line at a time refuses a
correctly typed command. Export and the command it unlocks share ONE line.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The refused form: an ack assignment PREFIXING a command. Prose about the flag
#: ("AI_HATS_PLAN_ACK=1 is not set") states, it does not prescribe — so the match
#: is anchored on a command word, and `export …` is the supported spelling.
INLINE_GRANT = re.compile(r"(?<!export )AI_HATS_[A-Z0-9_]*ACK=1\s+(rack|ai-hats|git|python)\b")

#: The forgotten form: an `export` prescribed as a step of its own. Only the
#: literal command spelling counts — prose merely naming the flag prescribes
#: nothing, and an ack-free follow-up may keep its own line (HATS-596).
LONE_EXPORT = re.compile(r"export AI_HATS_[A-Z0-9_]*ACK=1")

#: Every surface that tells a human or an agent how to satisfy a consent gate.
ADVICE_SITES = (
    "packages/ai-hats-rack/src/ai_hats_rack/extensions/plan.py",
    "src/ai_hats/rack_cli_provider.py",
    "src/ai_hats/cli/worktree.py",
    "packages/ai-hats-library/src/ai_hats_library/core/skills/worktree-isolation/SKILL.md",
)


def test_the_advice_sites_all_exist():
    """Green must mean 'checked and clean', never 'matched nothing'."""
    missing = [p for p in ADVICE_SITES if not (REPO_ROOT / p).exists()]
    assert not missing, f"advice sites moved — update ADVICE_SITES: {missing}"


def test_no_consent_gate_prescribes_the_inline_grant():
    offenders: dict[str, list[str]] = {}
    for rel in ADVICE_SITES:
        hits = [
            line.strip()
            for line in (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
            if INLINE_GRANT.search(line)
        ]
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "these messages prescribe an inline ack the guard refuses — say "
        f"`export AI_HATS_..._ACK=1 && <cmd>` instead: {offenders}"
    )


def test_the_detector_tells_the_forms_apart():
    """Without this, a detector matching nothing would pass the test above."""
    assert INLINE_GRANT.search("AI_HATS_PLAN_ACK=1 rack transition HATS-1 execute")
    assert INLINE_GRANT.search("  AI_HATS_MERGE_ACK=1 ai-hats wt merge task/x")
    assert not INLINE_GRANT.search("export AI_HATS_PLAN_ACK=1")
    # Prose ABOUT the flag states a fact; it prescribes nothing.
    assert not INLINE_GRANT.search("AI_HATS_PLAN_ACK=1 is not set in environment.")


def test_no_consent_recipe_leaves_its_export_alone_on_a_line():
    """An export the next line cannot see grants nothing (HATS-1654)."""
    offenders: dict[str, list[str]] = {}
    for rel in ADVICE_SITES:
        hits = [
            line.strip()
            for line in (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
            if LONE_EXPORT.search(line) and "&&" not in line
        ]
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "these recipes spend the export on a line of its own — chain it to the "
        f"command it unlocks (`export … && ai-hats wt merge …`): {offenders}"
    )


def test_the_lone_export_detector_tells_the_forms_apart():
    """Without this, a detector matching nothing would pass the test above."""
    lone = "  export AI_HATS_MERGE_ACK=1"
    chained = "  export AI_HATS_MERGE_ACK=1 && ai-hats wt merge task/x"
    assert LONE_EXPORT.search(lone) and "&&" not in lone
    assert LONE_EXPORT.search(chained) and "&&" in chained
    # Prose naming the flag is not a recipe step, chained or not.
    assert not LONE_EXPORT.search("a supervisor-exported `AI_HATS_MERGE_ACK=1` flows in")
    assert not LONE_EXPORT.search("AI_HATS_PLAN_ACK=1 is not set in environment.")
