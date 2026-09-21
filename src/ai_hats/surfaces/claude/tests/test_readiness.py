"""The claude readiness probe: ``claude auth status`` read as canonical signals.

Only a parsed ``loggedIn: false`` refuses; every shape the probe cannot read is a
``Notice`` — the engine may still carry a bundled CLI, and a wrong refusal costs
more than a late failure.
"""

from __future__ import annotations

import json
import subprocess
from subprocess import CompletedProcess

import pytest
from ai_hats_observe.canonical.signals import (
    Notice,
    PersonActionRequired,
    PersonMustAct,
    WorthRecording,
)

from ai_hats.surfaces.claude.readiness import SOURCE, readiness_findings

_ENV = {"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "/tmp/claude-home"}


def _status(**fields: object) -> str:
    return json.dumps({"apiProvider": "firstParty", **fields}, indent=2)


def _run_returning(stdout: str, code: int):
    def run(command, **kwargs):
        run.calls.append((command, kwargs))
        return CompletedProcess(command, code, stdout, "")

    run.calls = []  # type: ignore[attr-defined]
    return run


def test_not_logged_in_is_a_reauthenticate_finding() -> None:
    run = _run_returning(_status(loggedIn=False, authMethod="none"), 1)

    findings = readiness_findings(_ENV, which=lambda name, path=None: "/bin/claude", run=run)

    assert len(findings) == 1
    finding = findings[0]
    assert isinstance(finding, PersonActionRequired)
    assert finding.reason is PersonMustAct.REAUTHENTICATE
    assert finding.source == SOURCE
    assert finding.raw_code == "none"
    assert "claude auth login" in (finding.detail or "")


@pytest.mark.parametrize(
    "fields",
    [
        {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"},
        {"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY"},
        {"loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock"},
    ],
    ids=["oauth", "api-key", "bedrock"],
)
def test_logged_in_shapes_are_silent(fields: dict[str, object]) -> None:
    run = _run_returning(_status(**fields), 0)

    assert readiness_findings(_ENV, which=lambda name, path=None: "/bin/claude", run=run) == []


def test_the_probe_runs_auth_status_in_the_given_environment() -> None:
    run = _run_returning(_status(loggedIn=True, authMethod="claude.ai"), 0)

    readiness_findings(_ENV, which=lambda name, path=None: "/bin/claude", run=run)

    ((command, kwargs),) = run.calls
    assert command == ["/bin/claude", "auth", "status"]
    assert kwargs["env"] == _ENV
    assert kwargs["timeout"] > 0


def test_no_binary_on_path_is_a_notice_and_runs_nothing() -> None:
    findings = readiness_findings(
        _ENV,
        which=lambda name, path=None: None,
        run=lambda *a, **k: pytest.fail("no binary — nothing to run"),
    )

    assert [type(f) for f in findings] == [Notice]
    assert findings[0].reason is WorthRecording.SURFACE_WARNING
    assert findings[0].source == SOURCE
    assert "not on PATH" in (findings[0].detail or "")


def test_a_cli_without_the_verb_is_a_notice_not_a_refusal() -> None:
    run = _run_returning("", 1)  # older CLI: `error: unknown command` on stderr, no JSON

    findings = readiness_findings(_ENV, which=lambda name, path=None: "/bin/claude", run=run)

    assert [type(f) for f in findings] == [Notice]
    assert "auth status" in (findings[0].detail or "")


@pytest.mark.parametrize(
    "exc",
    [OSError("exec format error"), subprocess.TimeoutExpired(["claude"], 10)],
    ids=["oserror", "timeout"],
)
def test_a_probe_that_cannot_run_is_a_notice(exc: Exception) -> None:
    def run(command, **kwargs):
        raise exc

    findings = readiness_findings(_ENV, which=lambda name, path=None: "/bin/claude", run=run)

    assert [type(f) for f in findings] == [Notice]
    assert type(exc).__name__ in (findings[0].detail or "")


def test_a_logged_out_status_never_leaks_the_command_output() -> None:
    run = _run_returning(_status(loggedIn=False, authMethod="none", email="x@y.z"), 1)

    (finding,) = readiness_findings(_ENV, which=lambda name, path=None: "/bin/claude", run=run)

    assert "x@y.z" not in (finding.detail or "")
