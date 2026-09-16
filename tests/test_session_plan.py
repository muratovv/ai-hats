"""One session on the plan: what planning is told about the machine, and the
pair a launch is (ADR-0036 D2, D4)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from ai_hats.materialization import WriteKind
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surface_registry import get_surface
from ai_hats.surfaces import apply
from ai_hats.surfaces.plan import (
    Host,
    Launched,
    LaunchFlags,
    Prompt,
    PromptBlock,
    PromptMember,
)


def test_the_host_is_probed_once_for_every_command_the_gate_can_wrap(tmp_path: Path):
    """Planning never calls ``which``: the stage before it resolves every
    wrappable command and hands the result in as a value."""
    bins = tmp_path / "bin"
    bins.mkdir()
    for name in ("rack", "ai-hats"):
        (bins / name).write_text("#!/bin/sh\n")
        (bins / name).chmod(0o700)
    wrapper_bin = tmp_path / "sessions" / "s1" / "consent-wrapper" / "bin"
    wrapper_bin.mkdir(parents=True)
    (wrapper_bin / "rack").write_text("#!/bin/sh\n")
    (wrapper_bin / "rack").chmod(0o700)
    environ = {"PATH": os.pathsep.join([str(wrapper_bin), str(bins)])}

    host = probe_host(environ, python="/opt/py/bin/python3")

    assert host == Host(
        python=Path("/opt/py/bin/python3"),
        path=str(bins),
        commands={"ai-hats": bins / "ai-hats", "rack": bins / "rack"},
    )
    assert host.digest == probe_host(environ, python="/opt/py/bin/python3").digest


def test_a_command_missing_from_the_path_is_absent_not_empty(tmp_path: Path):
    host = probe_host({"PATH": str(tmp_path)}, python="/opt/py/bin/python3")
    assert host.commands == {}


def test_a_launch_is_an_argv_or_an_option_document_never_both():
    with pytest.raises(ValueError):
        Launched(args=("claude",), sdk_options={"model": "x"}, env={}, prompt="")
    with pytest.raises(ValueError):
        Launched(args=None, sdk_options=None, env={}, prompt="")
    launched = Launched(args=("claude",), sdk_options=None, env={"A": "1"}, prompt="hi")
    assert launched.args == ("claude",) and launched.sdk_options is None


def test_launch_flags_default_to_what_a_hitl_launch_needs():
    flags = LaunchFlags(session_id="s", trace_path="t", root_pid="1", provider_session_id="u")
    assert flags.extra_args == () and flags.work_dir is None and flags.brief is None
    assert flags.model is None and flags.claim is True


# ── claude on the real maintainer: pure planning, idempotent application ─────


@pytest.fixture
def maintainer(tmp_path: Path):
    """Function-scoped: a module-scoped assembler would be built before the
    per-test user-home isolation and read the developer's roots."""
    from ai_hats.assembler import Assembler
    from ai_hats.materialize import compose_to_run
    from ai_hats.surfaces import adapt

    project = tmp_path / "proj"
    project.mkdir()
    asm = Assembler(project)
    result = compose_to_run(asm, "maintainer")
    composition = adapt(
        result,
        identity="maintainer",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )
    return asm, composition


def _claude(asm, composition, root: Path, run_mode: RunMode):
    return get_surface("claude").plan(
        composition,
        run_mode=run_mode,
        policy=SessionPolicy(),
        root=root,
        layout=asm.layout,
        host=probe_host(),
    )


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_planning_for_one_root_is_the_same_before_and_after_it_is_applied(
    maintainer, tmp_path: Path, run_mode: RunMode
):
    """Planning reads nothing the application wrote (ADR-0036 D2)."""
    asm, composition = maintainer
    root = tmp_path / "sessions" / "s1"
    before = _claude(asm, composition, root, run_mode)
    apply(before)
    after = _claude(asm, composition, root, run_mode)
    assert before == after and before.digest == after.digest
    assert len(before.entries) > 40, "the sample composes dozens of skills"
    extra = dataclasses.replace(composition.skills[0], name="skills::extra")
    more = dataclasses.replace(composition, skills=(*composition.skills, extra))
    assert _claude(asm, more, root, run_mode) != before, "a changed input must show"


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_applying_the_plan_twice_reaches_no_primitive(
    maintainer, tmp_path: Path, writes: list[str], run_mode: RunMode
):
    asm, composition = maintainer
    root = tmp_path / "sessions" / "s1"
    plan = _claude(asm, composition, root, run_mode)
    apply(plan)
    writes.clear()
    assert apply(plan).changed is False
    assert writes == []

    hook = next(e for e in plan.entries if e.kind is WriteKind.COPY_TREE and e.target.name == "safety-guard")
    gate = hook.target / "hooks" / "safety_gate.py"
    gate.unlink()  # safe-delete: ok a session mirror the test owns
    writes.clear()
    assert apply(plan).changed is True and gate.is_file(), "the mirror heals by re-application"
    assert writes == ["copy2"]

    reworded = dataclasses.replace(
        composition,
        prompt=Prompt(
            (
                PromptBlock(None, (PromptMember("maintainer::prompt", "# other\n", None),)),
                *composition.prompt.blocks[1:],
            )
        ),
    )
    other = _claude(asm, reworded, root, run_mode)
    writes.clear()
    apply(other)
    assert writes == ["write_bytes"], "one changed byte sequence, one write"
