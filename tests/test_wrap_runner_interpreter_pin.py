"""Seam tests for the interpreter-pin notice.

A venv built on the wrong interpreter used to reach the user in silence and
surface as an unrelated failure at the worst moment — one such run was spent on
a blocked `git commit`. It must be said out loud at session start, and it must
never block: the venv still runs, it just runs somewhere untested.

The cases below call `interpreter_pin_notices` with the reads stated as
arguments; only the last one drives the method, and it patches nothing.
"""

import sys

import pytest

from ai_hats import wrap_runner
from ai_hats.constants import PINNED_PYTHON
from ai_hats.paths import runs_dir
from ai_hats.wrap_runner import WrapRunner, interpreter_pin_notices

OFF_PIN = "3.11"


def _runner(project):
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats.hooks_manager import HooksManager
    from ai_hats.models import ProjectConfig
    from ai_hats_core import CompositionResult
    from ai_hats_observe import SessionManager, SidecarTracer

    hooks = HooksManager(project, ProjectConfig(), resolve_provider=lambda name: None)
    payload = CompositionPayload(
        result=CompositionResult(name="t", priorities=[], rules=[], skills=[], injections=[]),
        provider=None,
        effective_role="t",
        hooks=hooks,
    )
    return WrapRunner(
        project,
        payload,
        session_mgr=SessionManager(project, runs_dir=runs_dir(project)),
        tracer_factory=SidecarTracer,
    )


@pytest.fixture
def runner(tmp_path):
    return _runner(tmp_path)


def test_the_pinned_interpreter_says_nothing():
    assert interpreter_pin_notices(PINNED_PYTHON, venv="/anywhere", managed=True) == []


def test_an_older_interpreter_warns():
    notices = interpreter_pin_notices(OFF_PIN, venv="/anywhere", managed=True)

    assert [n.level for n in notices] == ["warn"]
    assert OFF_PIN in notices[0].text
    assert PINNED_PYTHON in notices[0].text


def test_a_newer_interpreter_warns_too():
    """Ahead of the pin is still outside the matrix — the dev venv drifted to
    3.14 while the pin sat on 3.11, which is how nobody noticed."""
    assert [n.level for n in interpreter_pin_notices("3.99", venv="/anywhere", managed=True)] == [
        "warn"
    ]


def test_the_managed_venv_gets_the_command_that_rebuilds_it():
    """House format: exactly one `Fix:` line, self-contained enough to copy.
    `self update` is NOT it — on a current sha it rebuilds nothing."""
    text = interpreter_pin_notices(OFF_PIN, venv="/proj/.agent/ai-hats/.venv", managed=True)[0].text

    fix = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("Fix:")]
    assert fix == [f"Fix: {wrap_runner._REPAIR_CMD}"]


def test_a_venv_ai_hats_did_not_build_is_not_offered_a_rebuild():
    """`--repair` deletes the managed venv and leaves a user-owned one alone, so
    offering it to an override / editable install would be a command that lies."""
    text = interpreter_pin_notices(OFF_PIN, venv="/usr/local/venv", managed=False)[0].text

    assert "Fix:" not in text
    assert "will not touch it" in text


def test_the_warning_names_no_ai_hats_subcommand():
    """The first cut said `ai-hats self update --reinstall`: a flag click rejects
    with exit 2, and `self update` cannot be relied on to move a venv onto the pin
    (a current sha reuses the venv it runs from). The remedy is the rebuild — name
    a CLI command here again only after checking it parses AND that it rebuilds."""
    text = interpreter_pin_notices(OFF_PIN, venv="/anywhere", managed=True)[0].text

    assert "ai-hats self" not in text


def test_the_venv_path_reaches_the_reader():
    """The path is the one thing the operator cannot guess, and it is the read
    the seam takes as an argument — so a caller passing the wrong one is visible."""
    text = interpreter_pin_notices(OFF_PIN, venv="/tmp/some/venv", managed=False)[0].text

    assert "venv: /tmp/some/venv" in text


def test_this_very_session_is_on_the_pin_or_says_so(runner):
    """Drives the method, patches nothing: whatever interpreter runs the suite,
    the check agrees with it. Guards against a message that only renders under a
    fabricated version."""
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    notices = runner._check_interpreter_pin()

    assert notices == [] if running == PINNED_PYTHON else running in notices[0].text


def test_the_method_carries_the_verdict_out_of_the_seam(runner):
    """The wiring, stated: with the running version supplied, the method must
    return the seam's verdict rather than an empty list. Without this the whole
    file passes while `_check_interpreter_pin` returns `[]` unconditionally —
    the on-pin case above cannot tell those apart.
    """
    notices = runner._check_interpreter_pin(running=OFF_PIN)

    assert [n.level for n in notices] == ["warn"]
    assert OFF_PIN in notices[0].text
