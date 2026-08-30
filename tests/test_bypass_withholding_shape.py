"""What the withholding seam does with the names its shape rule cannot see.

``withheld_from_subagent`` recognises an approval by SUFFIX. Four production names
carry a bypass verb somewhere else and so answer False. Three of them are RIGHT to
answer False and this file records why per name; the fourth is a real escape hatch
and is named in ``BYPASS_FLAGS_OFF_CONVENTION``. The scan at the bottom is what
keeps that register from becoming the roster that failed open: a fifth such name
lands as a failure here rather than as a silent inheritance.
"""  # comment-length: allow — the split verdict IS this file's contract

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_hats.constants import (
    BYPASS_FLAG_SUFFIXES,
    BYPASS_FLAGS_NOT_INHERITED,
    BYPASS_FLAGS_OFF_CONVENTION,
    CONSENT_OWNED_KEYS,
    withheld_from_subagent,
)
from ai_hats.self_location import SKIP_ENV_VAR

REPO_ROOT = Path(__file__).resolve().parent.parent

# comment-length: allow — each entry is a ruling, and the reason is the ruling.
#: Verb-bearing names production reads that are deliberately NOT approvals, each with
#: what withholding it would actually break. A verb in the name is not a verdict: two
#: of these DISABLE something dangerous, so blanking them arms a child its parent
#: disarmed, and the third approves nothing at all.
NOT_AN_APPROVAL = {
    "AI_HATS_SKIP_RETIRED_PRUNE": (
        "a brake, not a bypass: set, it suppresses `uv pip uninstall`. Blanking it "
        "hands a child the prune of the venv its parent was protecting"
    ),
    "HATS_SKIP_RETRO": (
        "the auto-retro recursion guard, which `_spawn_session_reviewer_background` "
        "SETS into the child env on purpose. Blanking it restores the spawn loop"
    ),
    "AI_HATS_NO_UPDATE_CHECK": (
        "a knob: it silences a network probe and a banner and approves nothing, so "
        "withholding it would change a child's behaviour rather than its consent"
    ),
}

#: A verb, as a whole segment, in the vocabulary a bypass name draws from.
BYPASS_VERBS = frozenset({"ACK", "NO", "OFF", "SKIP", "YOLO"})

_ENV_NAME = re.compile(r"\b(?:AI_)?HATS_[A-Z0-9_]+")


def _unclassified(text: str) -> set[str]:
    """Verb-bearing names in ``text`` that neither the shape test nor a register answers for."""
    answered = set(NOT_AN_APPROVAL) | CONSENT_OWNED_KEYS
    return {
        name
        for name in _ENV_NAME.findall(text)
        if BYPASS_VERBS & set(name.split("_"))
        and not withheld_from_subagent(name)
        and name not in answered
    }


def _production_sources() -> list[Path]:
    """Executables that can read an approval — wider than the shipped hooks, as `scripts/` holds gates too."""
    roots = [REPO_ROOT / "src", REPO_ROOT / "scripts", *sorted(REPO_ROOT.glob("packages/*/src"))]
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".py", ".sh") or not path.is_file():
                continue
            # An area's tests live inside its own folder; a fixture is not a gate.
            if "tests" in path.relative_to(root).parts:
                continue
            out.append(path)
    return out


def test_the_self_location_hatch_is_withheld_from_a_sub_agent() -> None:
    """The one front-verb name that IS an approval: an operator's documented escape hatch.

    It disables the guard outright, and the guard's own fail-open bias is not a reason
    to let it cross — a normally launched child runs a managed prefix and is sanctioned
    by shape, so withholding costs it nothing and cannot brick it.
    """
    assert SKIP_ENV_VAR in BYPASS_FLAGS_OFF_CONVENTION, "register it by the home's spelling"
    assert withheld_from_subagent(SKIP_ENV_VAR)
    assert SKIP_ENV_VAR in BYPASS_FLAGS_NOT_INHERITED, "and name it in the launch record"


def test_positive_control_only_the_register_can_be_saying_yes() -> None:
    """Control for the test above: the True cannot be coming from the shape rule.

    Doubles as the rename gate. Respell the hatch with a bypass suffix — option (a) —
    and this fires, saying the register entry is now dead weight the shape covers.
    """
    for name in sorted(BYPASS_FLAGS_OFF_CONVENTION):
        assert not name.endswith(BYPASS_FLAG_SUFFIXES), (
            f"{name} now answers the shape test — drop it from BYPASS_FLAGS_OFF_CONVENTION"
        )
    assert withheld_from_subagent(SKIP_ENV_VAR)


@pytest.mark.parametrize(("name", "why"), sorted(NOT_AN_APPROVAL.items()))
def test_a_verb_in_the_name_does_not_make_it_an_approval(name: str, why: str) -> None:
    """Each ruling above, pinned — so a later widening of the shape cannot quietly reverse one."""
    assert not withheld_from_subagent(name), why
    assert name not in BYPASS_FLAGS_NOT_INHERITED, why


@pytest.mark.parametrize(
    "name", ["AI_HATS_NO_RAW_DESTRUCTIVE_SKIP", "AI_HATS_YOLO", "AI_HATS_SKIP_SELF_LOCATION_GUARD"]
)
def test_positive_control_the_predicate_still_says_yes_to_an_approval(name: str) -> None:
    """Control for the rulings: a predicate broken into always answering False passes them all."""
    assert withheld_from_subagent(name)


def test_no_verb_bearing_name_escapes_a_verdict() -> None:
    """A front-verb flag added next month fails here until someone rules on it.

    This is what separates the register from the roster that failed open: the shape
    test still holds every conventionally spelled flag, and the names it cannot see
    are a CLOSED vocabulary rather than a list somebody must remember to extend.
    """
    found: dict[str, list[str]] = {}
    for path in _production_sources():
        for name in _unclassified(path.read_text(encoding="utf-8", errors="ignore")):
            found.setdefault(name, []).append(path.relative_to(REPO_ROOT).as_posix())
    assert not found, (
        "production reads bypass-verb names no verdict covers:\n"
        + "\n".join(f"  {n}  <- {', '.join(s)}" for n, s in sorted(found.items()))
        + "\nRule on each: an approval goes in constants.BYPASS_FLAGS_OFF_CONVENTION "
        "(and the roster beside it); anything else goes in NOT_AN_APPROVAL here with "
        "the reason withholding it would be wrong."
    )


def test_positive_control_the_scan_reports_a_planted_off_convention_flag() -> None:
    """The assertion above is empty-set-shaped, so a scan that stopped seeing names passes in silence."""
    planted = "if os.environ.get('AI_HATS_SKIP_MERGE_GATE') == '1':"
    assert _unclassified(planted) == {"AI_HATS_SKIP_MERGE_GATE"}
    # The three ways a name is legitimately quiet: no verb (NON is not NO), the
    # suffix rule already answers, consent owns its own artefacts.
    quiet = "AI_HATS_NON_INTERACTIVE AI_HATS_SMOKE_SKIP AI_HATS_CONSENT_ACK"
    assert _unclassified(quiet) == set()


def test_every_recorded_verdict_still_names_a_live_flag() -> None:
    """A stale ruling is a standing permission for a name nobody sets any more."""
    corpus = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in _production_sources()
    )
    dead = sorted(name for name in NOT_AN_APPROVAL if name not in corpus)
    assert dead == [], f"NOT_AN_APPROVAL rules on {dead}, which production no longer reads"
