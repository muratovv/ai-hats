"""A consent gate must not prescribe the one form its guard refuses (HATS-1639).

`safety_gate.check_self_grant` denies an inline `AI_HATS_*_ACK=1 <cmd>` prefix, so
a refusal that spells the recipe that way sends the agent at a wall — the HATS-1294
shape `rule_pause_before_shared_state_write` names, and the class HATS-1630 closed
for the tool-hygiene nudge. Consent is the supervisor's, from the launching
environment; the recipe must say `export`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The refused form: an ack assignment PREFIXING a command. Prose about the flag
#: ("AI_HATS_PLAN_ACK=1 is not set") states, it does not prescribe — so the match
#: is anchored on a command word, and `export …` is the supported spelling.
INLINE_GRANT = re.compile(r"(?<!export )AI_HATS_[A-Z0-9_]*ACK=1\s+(rack|ai-hats|git|python)\b")

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
        f"`export AI_HATS_..._ACK=1` on its own line instead: {offenders}"
    )


def test_the_detector_tells_the_forms_apart():
    """Without this, a detector matching nothing would pass the test above."""
    assert INLINE_GRANT.search("AI_HATS_PLAN_ACK=1 rack transition HATS-1 execute")
    assert INLINE_GRANT.search("  AI_HATS_MERGE_ACK=1 ai-hats wt merge task/x")
    assert not INLINE_GRANT.search("export AI_HATS_PLAN_ACK=1")
    # Prose ABOUT the flag states a fact; it prescribes nothing.
    assert not INLINE_GRANT.search("AI_HATS_PLAN_ACK=1 is not set in environment.")
