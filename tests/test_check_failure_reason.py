"""HATS-1572 — what the checks channel says when a bound check does not pass.

Two classes that used to read alike and are not alike: a child that RAN and
refused, and a channel that never got to run one. The second used to arrive as
``hook script missing: <path>``, which sends the operator to fix a script when
what needs fixing is which bytes the session resolved.

The wording is this channel's. Every FACT the primitive established travels
through it untouched — the tests below pin that half as hard as the vocabulary,
because a message that reads well and drops the errno is the worse regression.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from ai_hats_core import ResolvedCheck

from ai_hats.check_points import check_failure_reason
from ai_hats.hook_exec import HookOutcomeKind, HookRun, HookVerdict
from ai_hats.session_identity import SessionIdentity


def _check(script: Path) -> ResolvedCheck:
    return ResolvedCheck(
        app="rack",
        path=("tasks",),
        run="quality::gates/done-gate.sh",
        at=("edge:review--done",),
        cargo={},
        on_error="refuse",
        script_path=script,
        declared_by="maintainer",
    )


def _run(kind: HookOutcomeKind, *, verdict: HookVerdict, code: int | None = None, **fields):
    return HookRun(
        verdict=verdict, exit_code=code, reason="<primitive wording>", kind=kind, **fields
    )


def _identity(tmp_path: Path) -> SessionIdentity:
    return SessionIdentity(
        id="sess-a",
        role="maintainer",
        provider="claude",
        project_dir=tmp_path,
        session_dir=tmp_path / "sessions" / "sess-a",
        skills_root=str(tmp_path / "mirror"),
    )


def test_a_refusal_carries_the_childs_own_words_and_nothing_else(tmp_path):
    """Class (a): the child ran and formed a verdict, so the verdict is its text."""
    reason = check_failure_reason(
        _check(tmp_path / "done-gate.sh"),
        _run(HookOutcomeKind.REFUSED, verdict=HookVerdict.REFUSE, said="no green marker\n", code=2),
        identity=None,
    )

    assert reason == "no green marker"


def test_a_refusal_that_spoke_only_on_stderr_still_speaks(tmp_path):
    """The primitive falls back to stderr for exactly this reason; a channel that
    reads ``said`` alone re-swallows the gate that reports the ordinary shell
    way, and then asserts — falsely — that it refused without saying why."""
    reason = check_failure_reason(
        _check(tmp_path / "done-gate.sh"),
        _run(
            HookOutcomeKind.REFUSED,
            verdict=HookVerdict.REFUSE,
            code=2,
            stderr="drain the review notes first\n",
        ),
        identity=None,
    )

    assert reason == "drain the review notes first"


def _mirrored(tmp_path: Path, *, mirror: str, live: str) -> ResolvedCheck:
    """A bound check whose session mirror and live library hold different bytes."""
    mirror_script = tmp_path / "mirror" / "quality" / "done-gate.sh"
    live_script = tmp_path / "library" / "quality" / "done-gate.sh"
    for script, text in ((mirror_script, mirror), (live_script, live)):
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text)
    return replace(_check(mirror_script), source_path=live_script)


def test_a_refusal_from_a_stale_mirror_says_so(tmp_path):
    """HATS-1651: the gate refused, and the bytes that refused are not the bytes
    the library ships — the operator has to be told which of the two to fix.

    Observed 2026-08-13: an Aug-10 mirror keyed the marker on the commit while
    the live writer keyed it on the tree, so a genuinely green gate was refused
    with "no green quality-gate marker for <sha>" — naming a key that no longer
    existed in the scheme the writer used. Nothing in that message could lead
    anyone to the stale mirror; the workaround was found by accident, running
    the merge outside any session.
    """
    reason = check_failure_reason(
        _mirrored(tmp_path, mirror='grep -qx "sha=$3"', live='grep -qx "tree=$tree"'),
        _run(
            HookOutcomeKind.REFUSED,
            verdict=HookVerdict.REFUSE,
            said="no green marker for eea4f6d0\n",
            code=2,
        ),
        identity=_identity(tmp_path),
    )

    assert reason.startswith("no green marker for eea4f6d0"), (
        f"the child's own verdict must stay first and verbatim:\n{reason}"
    )
    assert "stale" in reason
    assert "'sess-a'" in reason
    assert str(tmp_path / "mirror" / "quality" / "done-gate.sh") in reason
    assert str(tmp_path / "library" / "quality" / "done-gate.sh") in reason
    assert "Restart the session" in reason


def test_the_note_reaches_the_callers_that_never_pass_an_identity(tmp_path, monkeypatch):
    """The hole this test exists for: both production callers
    (``wt_lifecycle``, ``rack_consumers``) call with ``identity`` defaulted, so a
    note that only fires on an explicitly-passed identity is unreachable code
    with green tests around it. Read from the environment, exactly as the
    sibling ``SCRIPT_MISSING`` notice does.
    """
    for key, value in _identity(tmp_path).to_env().items():
        monkeypatch.setenv(key, value)

    reason = check_failure_reason(
        _mirrored(tmp_path, mirror="frozen", live="current"),
        _run(HookOutcomeKind.REFUSED, verdict=HookVerdict.REFUSE, said="no green marker\n", code=2),
    )

    assert "stale" in reason, f"the note never fires the way production calls it:\n{reason}"


def test_a_refusal_from_a_current_mirror_adds_nothing(tmp_path):
    """The note must stay silent when the bytes agree.

    A notice on every refusal is a notice nobody reads, and this one has to
    survive being read on the day it matters.
    """
    same = 'grep -qx "tree=$tree"'
    reason = check_failure_reason(
        _mirrored(tmp_path, mirror=same, live=same),
        _run(HookOutcomeKind.REFUSED, verdict=HookVerdict.REFUSE, said="no green marker\n", code=2),
        identity=_identity(tmp_path),
    )

    assert reason == "no green marker"


def test_outside_a_session_no_mirror_can_be_stale(tmp_path):
    """Live resolution has no frozen half, so the note would name a non-problem."""
    reason = check_failure_reason(
        _mirrored(tmp_path, mirror="one", live="another"),
        _run(HookOutcomeKind.REFUSED, verdict=HookVerdict.REFUSE, said="no green marker\n", code=2),
        identity=None,
    )

    assert reason == "no green marker"


def test_absent_bytes_name_the_resolution_not_the_script(tmp_path):
    """Class (b): the gate did not run at all, and the operator is told where the
    bytes were looked for, that the live library is not a fallback, and what to
    do next — none of which is a fact about the script."""
    reason = check_failure_reason(
        _check(tmp_path / "mirror" / "quality" / "done-gate.sh"),
        _run(HookOutcomeKind.SCRIPT_MISSING, verdict=HookVerdict.CORRUPT),
        identity=_identity(tmp_path),
    )

    assert "the check did not run" in reason
    assert "'sess-a'" in reason
    assert str(tmp_path / "mirror") in reason
    assert "claude" in reason  # whose mirror it is: the session's surface, not the config's
    assert "NOT used as a fallback" in reason
    assert "Restart the session" in reason


def test_outside_a_session_absent_bytes_name_the_live_library(tmp_path):
    """No session means no mirror to blame: the file is simply not shipped."""
    reason = check_failure_reason(
        _check(tmp_path / "gone.sh"),
        _run(HookOutcomeKind.SCRIPT_MISSING, verdict=HookVerdict.CORRUPT),
        identity=None,
    )

    assert "the check did not run" in reason
    assert "live library" in reason
    assert "no longer ships it" in reason


@pytest.mark.parametrize(
    ("kind", "verdict", "code", "expected"),
    [
        (HookOutcomeKind.NOT_EXECUTABLE, HookVerdict.CORRUPT, None, "chmod +x"),
        (HookOutcomeKind.TIMED_OUT, HookVerdict.BROKE, None, "past its budget"),
        (HookOutcomeKind.EXITED, HookVerdict.BROKE, 1, "the check broke"),
        (HookOutcomeKind.SIGNALLED, HookVerdict.BROKE, -15, "was killed"),
        (HookOutcomeKind.COMMAND_NOT_FOUND, HookVerdict.CORRUPT, 127, "command not found"),
        (HookOutcomeKind.LOG_UNUSABLE, HookVerdict.CORRUPT, None, "log could not be opened"),
        (HookOutcomeKind.EXEC_FAILED, HookVerdict.CORRUPT, None, "could not be executed"),
        (HookOutcomeKind.NO_TIME_LEFT, HookVerdict.BROKE, None, "never started"),
    ],
)
def test_no_outcome_calls_a_binding_line_a_hook(tmp_path, kind, verdict, code, expected):
    """``hook`` names real channels here — git_hooks, runtime_hooks, worktree — so
    a binding line must never borrow the word, on any outcome."""
    reason = check_failure_reason(
        _check(tmp_path / "g.sh"), _run(kind, verdict=verdict, code=code), identity=None
    )

    assert expected in reason
    assert "hook" not in reason
    assert "quality::gates/done-gate.sh" in reason  # the binding is still named


def test_every_outcome_has_words_of_its_own():
    """The exhaustiveness the ``kind`` enum exists to make checkable: a member
    added without a phrase would otherwise degrade to a contentless sentence
    with the suite green — a fallback that cannot report."""
    from ai_hats.check_points import _sayings

    worded = set(_sayings())
    branch_handled = {
        HookOutcomeKind.PASSED,  # not a failure: `check_failure_reason` returns ""
        HookOutcomeKind.REFUSED,
        HookOutcomeKind.SCRIPT_MISSING,
    }

    assert worded | branch_handled == set(HookOutcomeKind)


@pytest.mark.parametrize(
    ("kind", "code", "detail", "fact"),
    [
        (HookOutcomeKind.LOG_UNUSABLE, None, "(PermissionError): [Errno 13] /ro/x.log", "Errno 13"),
        (HookOutcomeKind.EXEC_FAILED, None, "(OSError): [Errno 26] Text file busy", "Errno 26"),
        (HookOutcomeKind.TIMED_OUT, None, "after 0.3s", "0.3s"),
        (HookOutcomeKind.SIGNALLED, -9, "signal 9", "signal 9"),
    ],
)
def test_the_primitives_fact_survives_this_channels_wording(tmp_path, kind, code, detail, fact):
    """Rewording is not the same as discarding. Which path, which errno, which
    signal, how long a budget — each is the difference between two diagnoses, and
    the operator loses it if the channel keeps only its own phrase."""
    reason = check_failure_reason(
        _check(tmp_path / "g.sh"),
        _run(kind, verdict=HookVerdict.BROKE, code=code, detail=detail),
        identity=None,
    )

    assert fact in reason
    assert "hook" not in reason


def test_a_child_exit_126_does_not_send_the_operator_to_chmod_this_script(tmp_path):
    """A non-executable binding is refused at composition, so a 126 from a child
    that RAN means something IT invoked could not be executed. The chmod advice
    belongs to the pre-flight case alone, told apart by the absent exit code."""
    reason = check_failure_reason(
        _check(tmp_path / "g.sh"),
        _run(HookOutcomeKind.NOT_EXECUTABLE, verdict=HookVerdict.CORRUPT, code=126),
        identity=None,
    )

    assert "chmod" not in reason
    assert "something it invoked" in reason


def test_a_cut_tail_says_so_and_points_at_the_log(tmp_path):
    """R3.3 keeps the child's words; a partial verdict read as a whole one is the
    silent truncation HATS-1137 already paid for once."""
    log = tmp_path / "edge-review--done~abc.log"
    reason = check_failure_reason(
        _check(tmp_path / "g.sh"),
        _run(
            HookOutcomeKind.REFUSED,
            verdict=HookVerdict.REFUSE,
            code=2,
            said="…the tail only",
            truncated=True,
            output_size=9000,
            log_path=log,
        ),
        identity=None,
    )

    assert "output truncated" in reason
    assert str(log) in reason


def test_a_bytes_absent_run_is_not_worded_as_a_refusal(tmp_path):
    """The distinction the card exists for, asserted as a difference."""
    check = _check(tmp_path / "g.sh")
    refused = check_failure_reason(
        check,
        _run(HookOutcomeKind.REFUSED, verdict=HookVerdict.REFUSE, said="drain the notes", code=2),
        identity=None,
    )
    absent = check_failure_reason(
        check, _run(HookOutcomeKind.SCRIPT_MISSING, verdict=HookVerdict.CORRUPT), identity=None
    )

    assert refused == "drain the notes"
    assert "did not run" in absent and "did not run" not in refused
