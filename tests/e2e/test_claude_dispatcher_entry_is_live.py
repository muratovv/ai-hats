"""e2e (HATS-1874)

flow:   a composed claude session, its own settings.json entry run the way the
        harness runs it
cmds:
    sh -c "<the command settings.json holds>"
expect: the entry starts the installed dispatcher module, which reads the
        session manifest and lets the composed gate judge the call
why:    every hook-chain test now reads the composed rows from the manifest, so
        all of them could stay green while settings.json wires a dispatcher
        nothing executes. This is the test that refuses that world
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.sessions import stand_in_session

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats.surfaces.claude.provider import ClaudeSurface

SESSION_ID = "sid-dispatcher-entry"


DENY = '#!/bin/sh\nprintf "%s\\n" "the gate itself spoke" >&2\nexit 2\n'

TICKET = (
    "#!/bin/sh\ncat >/dev/null\n"
    "printf '%s' '"
    + json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "confirm this",
                "updatedInput": {"command": "echo REWRITTEN_BY_TICKET"},
            }
        }
    )
    + "'\n"
)


@pytest.fixture
def session(tmp_path: Path, request):
    """A composition whose single gate speaks, so a verdict proves the whole
    path ran: entry -> module -> manifest -> the gate's own bytes."""
    project = tmp_path / "project"
    project.mkdir()
    skill_dir = tmp_path / "skills" / "gatekeeper"
    (skill_dir / "hooks").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: gatekeeper\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Bash\n"
        "        script: hooks/deny.sh\n"
        "---\n"
        "# Gatekeeper\n",
        encoding="utf-8",
    )
    gate = skill_dir / "hooks" / "deny.sh"
    gate.write_text(getattr(request, "param", DENY))
    gate.chmod(0o755)

    result = CompositionResult(
        name="r",
        priorities=[],
        rules=[],
        skills=[
            ResolvedComponent(
                name="gatekeeper", component_type=ComponentKind.SKILL, source_path=skill_dir
            )
        ],
        injections=[],
    )
    artifacts = ClaudeSurface().build_session_artifacts(
        ProjectLayout.at(project),
        result,
        SESSION_ID,
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= artifacts.extra_env | {"AI_HATS_PYTHON": sys.executable}
    env.pop("AI_HATS_GATE_BROKEN_ACK", None)
    return project, env


def _entry_command(project: Path) -> str:
    settings = json.loads(
        (ProjectLayout.at(project).cache.session(SESSION_ID) / "settings.json").read_text()
    )
    entries = settings["hooks"]["PreToolUse"]
    assert len(entries) == 1, f"one entry per event is what HATS-1874 delivers; got: {entries}"
    return entries[0]["hooks"][0]["command"]


@pytest.mark.integration
def test_the_entry_settings_json_holds_starts_the_installed_dispatcher(session) -> None:
    project, env = session

    done = subprocess.run(  # noqa: S602 - the settings.json string, run as the harness runs it
        _entry_command(project),
        shell=True,
        input=json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
            }
        ),
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "No module named" not in done.stderr, (
        f"the entry names a module this interpreter cannot start:\n{done.stderr}"
    )
    spoken = json.loads(done.stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny", done.stdout
    assert "the gate itself spoke" in spoken["permissionDecisionReason"], done.stdout


@pytest.mark.integration
@pytest.mark.parametrize("session", [TICKET], indirect=True)
def test_a_consent_ticket_survives_the_live_entry(session) -> None:
    """The question and its rewrite are one capability — asked without the
    rewrite, the human approves the original line (HATS-1642). Proven here on
    the wiring a session actually gets, not on a hand-built manifest."""
    project, env = session

    done = subprocess.run(  # noqa: S602 - the settings.json string, run as the harness runs it
        _entry_command(project),
        shell=True,
        input=json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo ORIGINAL"},
            }
        ),
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    spoken = json.loads(done.stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "ask", done.stdout
    assert spoken["updatedInput"] == {"command": "echo REWRITTEN_BY_TICKET"}, (
        f"the question arrived without its ticket: {done.stdout}"
    )
