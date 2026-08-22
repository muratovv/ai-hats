"""e2e (HATS-1719)

flow:   a role whose consent is declared in the ARROW spelling, read across the
        process boundary by the composed PreToolUse chain
cmds:
    rack transition HATS-1 execute
    rack transition HATS-1 done
expect: the chain still raises the supervisor's question on both declared roads
why:    the guard is stdlib-only and cannot import the rack's parser. It used to
        cut the point name itself (`safety_gate.py`, `partition("--")`), and on an
        arrow that cut returns nothing — `declared_consent_targets()` empties and
        the question disappears on BOTH roads into master, in silence. That is
        defect A5 of HATS-1682, and this is the test that catches it: revert the
        envelope half of HATS-1719 and these assertions go red.
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import subprocess

import pytest

from _helpers.hook_chain import build_session_settings, run_chain  # noqa: E402
from _helpers.sessions import stand_in_session  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def arrow_project(shared_launcher, tmp_path_factory):
    """A real project composed with a role that DECLARES consent.

    ``assistant`` composes ``trait-agent``, whose rows are the two migrated to
    the arrow spelling — so the envelope under test is the shipped one, not a
    fixture written to match the assertion.
    """
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("arrow-consent-home"))

    project = tmp_path_factory.mktemp("arrow-consent-proj")
    result = subprocess.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(f"self init failed:\n{result.stdout}\n{result.stderr}")
    return project, env, build_session_settings(project)


def test_the_declaration_reaches_the_guard_with_its_ends_parsed(arrow_project):
    """The envelope's shape, at the boundary — the guard reads fields, not grammar."""
    project, env, _settings = arrow_project
    from ai_hats.assembler import Assembler
    from ai_hats.session_report import consent_entry

    consent = Assembler(project).composer.compose("assistant").consent
    rack_rows = [
        consent_entry(c)
        for c in consent
        if c.app == "consent_gate" and c.path == ("rack.transition",)
    ]

    assert rack_rows, "the assistant role declares no rack consent — the probe is blind"
    for row in rack_rows:
        assert "->" in row["selector"], f"declaration is not in the arrow spelling: {row}"
        # The FIELD, never a cut of the name here: re-implementing the grammar in
        # the probe is what let it stay green while the parser it guards answered
        # (None, None) for every row the composition produces (HATS-1790).
        assert row["to"], f"the envelope carries no parsed target, so the guard is blind: {row}"
        assert "point" not in row, f"the retired key is still written: {row}"


@pytest.fixture(scope="module")
def in_session(arrow_project):
    """The chain, run from inside a session that DECLARED consent.

    Without the envelope the guard has nothing to read and allows everything —
    which is correct, and would make every assertion below pass for the wrong
    reason. The envelope is written by the shared helper, from the role's real
    composition, so this test cannot agree with a fixture instead of the code.
    """
    project, env, settings = arrow_project
    return project, stand_in_session(dict(env), project, "sid-arrow"), settings


@pytest.mark.parametrize("target", ["execute", "done"])
def test_the_question_is_raised_on_a_road_declared_by_arrow(in_session, target):
    """The fail-under-revert half: revert the guard's envelope read and this goes
    QUIET rather than red elsewhere — which is why the assertion is on the
    question, not on some downstream refusal."""
    project, env, settings = in_session
    verdict = run_chain(project, f"rack transition HATS-1 {target}", settings=settings, env=env)

    assert verdict.gated, (
        f"the guard did NOT raise the consent question on '->{target}' — a declaration "
        f"in the arrow spelling reached it unread: {verdict}"
    )


def test_an_undeclared_road_is_still_not_gated(in_session):
    """Positive control: without it, a chain that gates EVERYTHING would pass the
    two assertions above while telling us nothing about the declaration."""
    project, env, settings = in_session
    verdict = run_chain(project, "rack transition HATS-1 blocked", settings=settings, env=env)

    assert not verdict.gated, f"an undeclared road was gated — the probe proves nothing: {verdict}"


@pytest.mark.parametrize(
    "command",
    [
        "/usr/local/bin/rack transition HATS-1 done",
        "python -m ai_hats_rack transition HATS-1 done",
        # HATS-1781: the table maps BOTH module spellings to `rack`, and the
        # boundary used to match the literal — so this one walked through while
        # the allow-rule lint called it guarded.
        "python -m ai_hats_rack.cli transition HATS-1 done",
        # A runner resolves the packaged binary, not the session-local wrapper.
        "uvx rack transition HATS-1 done",
        "command -p rack transition HATS-1 done",
        "/usr/local/bin/ai-hats wt merge task/hats-1",
        "python -m ai_hats wt merge task/hats-1",
    ],
)
def test_protected_operations_cannot_bypass_the_session_wrapper(in_session, command):
    project, env, settings = in_session

    verdict = run_chain(project, command, settings=settings, env=env)

    assert verdict.denied, f"wrapper bypass was not denied: {verdict}"
    assert "session wrapper" in verdict.reason
