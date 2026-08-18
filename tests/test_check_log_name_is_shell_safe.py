"""A check's log filename carries no shell metacharacter (HATS-1719).

The arrow put ``>`` into the event key, and the log name was built by replacing
``:`` alone — so the redirect character reached a path that refusal messages
hand to an operator to paste. Escaped through the module's own reversible
encoder rather than replaced, so two different events cannot collide on one
name (the HATS-1137 truncation defect).
"""

from __future__ import annotations

import string

from ai_hats_core import ResolvedCheck

from ai_hats.check_points import check_log_name

SAFE = frozenset(string.ascii_letters + string.digits + "._-~+%")


def _check(at: str) -> ResolvedCheck:
    return ResolvedCheck(
        app="rack",
        path=("tasks",),
        run="quality-gates/gate.sh",
        at=(at,),
        cargo={},
        on_error="refuse",
        script_path="/tmp/gate.sh",
        declared_by="trait-agent",
    )


def test_an_arrow_event_yields_a_shell_safe_log_name():
    name = check_log_name("review->done", _check("review->done"))

    assert ">" not in name
    assert set(name) <= SAFE, sorted(set(name) - SAFE)


def test_two_different_events_do_not_collide_on_one_name():
    row = _check("->done")
    assert check_log_name("review->done", row) != check_log_name("->done", row)
