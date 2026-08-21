"""HATS-1720's veto on the rows that survive ADR-0030 — the ones that RUN.

Carried over from `test_consent_declaration_contract.py`, which HATS-1755 deleted
along with rack's consent rows. The consent half of that veto went with them (its
guarantee now lives in the wrapper's own policy compiler, see
`test_consent_wrapper.py`), but the GATE half did not move anywhere: a `run:` row
on a wide output still locks a card where it stands, and nothing else asserted it
at unit level.
"""

from __future__ import annotations

import pytest

from ai_hats.check_points import CheckBindingError, resolve_checks
from ai_hats.models import parse_app_bindings

#: Both spellings of "every way out of this state" — the shape that turns a gate
#: into a lock-in, since a refusal then stands on `document`, `blocked`, `failed`,
#: `cancelled` and the reclaim self-loop alike.
_WIDE_OUTPUT = ["execute->", "ANY->ANY"]


def _rows(block, declared_by="trait-agent"):
    return list(parse_app_bindings(block, declared_by=declared_by))


def _gate_row(point: str, *, declared_by: str = "maintainer"):
    return _rows(
        {"rack": {"tasks": [{"run": "gate-skill/hooks/gate.sh", "at": [point]}]}},
        declared_by=declared_by,
    )


@pytest.mark.parametrize("point", _WIDE_OUTPUT)
def test_a_gate_on_a_wide_output_is_refused_and_says_what_it_would_cost(point):
    """The measured consequence, not a style rule: `on_error: warn` softens only a
    check that BROKE and `--force` relaxes the arrow while the check still runs,
    so a wide-output gate leaves no way out of the state."""
    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(_gate_row(point), [])

    said = str(exc.value)
    assert repr(point) in said, "the refusal must name the selector it refuses"
    assert "locks the card" in said, f"the refusal does not name the cost: {said}"


@pytest.mark.parametrize("point", ["->done", "review->done"])
def test_a_narrow_target_passes_the_veto_and_is_judged_on_its_merits(point):
    """The discriminator: a veto refusing every selector would pass the test above
    while disarming the shipped done-gate, which lives on `->done`. Reaching the
    skill lookup IS passing the veto."""
    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(_gate_row(point), [])

    said = str(exc.value)
    assert "composes no skill" in said, f"the row never reached script resolution: {said}"


def test_the_veto_is_asked_before_the_script_exists():
    """Order matters: the wide row names a skill nothing composes, and the veto
    still wins. Asked after resolution, it would refuse only the typos."""
    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(_gate_row("execute->"), [])

    assert "composes no skill" not in str(exc.value)
