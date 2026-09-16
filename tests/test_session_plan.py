"""One session on the plan: what planning is told about the machine, and the
pair a launch is (ADR-0036 D2, D4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai_hats.session_plan import probe_host
from ai_hats.surfaces.plan import Host, LaunchFlags, Launched


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
