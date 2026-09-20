"""No shipped text tells a reader to type a command the guard refuses.

`ack_prefix_guard.py` denies a Bash line binding an `AI_HATS_*_ACK/_OFF/_SKIP`
flag for the command after it, and 24 lines across 13 library files printed
exactly that as the way out of a block.

Every discrimination is asserted in both directions: a one-sided assertion
cannot tell a working scanner from one flagging everything, and the sanctioned
prose names the same flag and must survive.
"""

from __future__ import annotations

from pathlib import Path

from tests._self_grant_recipe import EXCLUDED_DIR, LIBRARY_RELPATH, findings, scan

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Verbatim from `pre-commit-privacy.sh` before this gate existed. The scanner's
#: positive control: a regex that stopped matching would otherwise report a
#: clean library and nobody would know.
KNOWN_RECIPE = "       AI_HATS_PRIVACY_ACK=1 git commit ..."

#: The sanctioned prose, verbatim from `pre_bash_shared_state_guard.sh` — same
#: flag, no instruction. It is what the swept files now say instead.
SANCTIONED = "  2. AI_HATS_SHARED_STATE_ACK=1 is present in the environment that launched the"


def test_the_refused_recipe_is_found_and_the_sanctioned_prose_is_not():
    """The one discrimination the gate rests on, both directions, one corpus."""
    hits = findings(f"{KNOWN_RECIPE}\n{SANCTIONED}\n")
    assert [f.line for f in hits] == [1], hits


def test_a_fenced_sample_is_not_a_hiding_place():
    """A recipe is an instruction wherever it sits — a fence does not excuse it."""
    assert findings("```bash\nAI_HATS_SMOKE_SKIP=1 git commit -m wip\n```\n")


def test_a_flag_with_no_command_after_it_is_prose():
    """`AI_HATS_X_OFF=1` named as a setting is what the guard's own text does."""
    assert not findings("Kill switch: AI_HATS_BACKLOG_GATE_OFF=1, exported.\n")
    assert not findings("`AI_HATS_LIFETIME_ACK=1 <command>` never reaches it.\n")


def test_a_flag_outside_the_convention_is_not_this_gates_business():
    """The guard matches on the `_ACK`/`_OFF`/`_SKIP` shape; so does this."""
    assert not findings("AI_HATS_CONSENT_TICKET=1 git commit ...\n")
    assert findings("AI_HATS_CONSENT_ACK=1 git commit ...\n")


def test_the_guards_own_home_is_excluded_and_nothing_else_is(tmp_path: Path):
    """Exclusion is one directory, and it is the one that defines the shape."""
    library = tmp_path / LIBRARY_RELPATH
    for home in (EXCLUDED_DIR, "git-mastery"):
        skill = library / "core" / "skills" / home / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(f"Last resort:\n{KNOWN_RECIPE}\n")
    reported = scan(tmp_path)
    assert len(reported) == 1, reported
    assert "git-mastery" in reported[0], reported


def test_the_shipped_library_prints_no_such_recipe():
    """The ratchet. A new one fails here, named by path and line."""
    reported = scan(REPO_ROOT)
    assert reported == [], "self-granted approval printed as a recipe:\n" + "\n".join(reported)
