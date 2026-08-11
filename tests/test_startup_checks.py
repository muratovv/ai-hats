"""The ``ai-hats:startup`` point: a declared gate that runs before the session.

HATS-1581. What is asserted here is the outcome contract, not the wiring: a row
that refuses stops the launch with a code of its own, a row that merely broke is
governed by ``on_error``, and a row whose script is missing is NOT softened by
``on_error: warn`` — the substrate being absent is not a downgradable failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core import ComponentKind, CompositionResult, ResolvedCheck, ResolvedComponent

from ai_hats.check_points import AI_HATS_APP, STARTUP_POINT
from ai_hats.startup_checks import run_startup_checks


def _skill(root: Path, body: str = "exit 0\n", name: str = "gate-skill") -> ResolvedComponent:
    skill_dir = root / "library" / "skills" / name
    skill_dir.mkdir(parents=True)
    script = skill_dir / "startup.sh"
    script.write_text(f"#!/usr/bin/env bash\n{body}")
    script.chmod(0o755)
    return ResolvedComponent(name=name, component_type=ComponentKind.SKILL, source_path=skill_dir)


def _row(skill: ResolvedComponent, *, on_error: str = "refuse") -> ResolvedCheck:
    return ResolvedCheck(
        app=AI_HATS_APP,
        path=(),
        run=f"{skill.name}/startup.sh",
        at=(STARTUP_POINT,),
        cargo={},
        on_error=on_error,
        script_path=skill.source_path / "startup.sh",
        declared_by="role-x",
    )


def _result(skill, *checks) -> CompositionResult:
    return CompositionResult(
        name="tester",
        priorities=["Reliability"],
        rules=[],
        skills=[skill],
        injections=["body"],
        checks=tuple(checks),
    )


def _run(tmp_path, skill, *checks, session_dir=None):
    return run_startup_checks(
        tmp_path,
        session_dir=session_dir or tmp_path / "session",
        compose=lambda _p: _result(skill, *checks),
    )


def test_a_refusing_row_stops_the_launch_with_its_own_code(tmp_path):
    """exit 2 is the refuse signal; the launch must not proceed, and the code must
    not be 130 — that is the preset SIGINT default, so reusing it would make a
    refused gate indistinguishable from the operator pressing Ctrl-C."""
    skill = _skill(tmp_path, body="echo 'backlog is dirty' >&2\nexit 2\n")

    with pytest.raises(SystemExit) as caught:
        _run(tmp_path, skill, _row(skill))

    assert caught.value.code != 0
    assert caught.value.code != 130


def test_a_broken_row_is_softened_by_warn(tmp_path):
    """Exit 1 is the script failing, not refusing — that IS downgradable, so a
    role that said `warn` gets a notice and a session."""
    skill = _skill(tmp_path, body="echo boom >&2\nexit 1\n")

    notices = _run(tmp_path, skill, _row(skill, on_error="warn"))

    assert [n.level for n in notices] == ["warn"]
    assert "on_error: warn" in notices[0].text


def test_a_missing_script_is_NOT_softened_by_warn(tmp_path):
    """`warn` softens a check that ran and broke, never one that could not run at
    all: an absent script means the gate never judged anything, and treating that
    as a warning is the silence this channel exists to remove (HookRun.downgradable
    is BROKE only, never CORRUPT)."""
    skill = _skill(tmp_path)
    (skill.source_path / "startup.sh").unlink()

    with pytest.raises(SystemExit) as caught:
        _run(tmp_path, skill, _row(skill, on_error="warn"))

    assert caught.value.code != 0


def test_a_passing_row_launches_silently(tmp_path):
    """The quiet case: nothing to say, nothing to hold for."""
    skill = _skill(tmp_path)

    assert _run(tmp_path, skill, _row(skill)) == []
