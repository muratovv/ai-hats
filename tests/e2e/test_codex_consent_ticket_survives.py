"""e2e (HATS-1858)

flow:   a gate answers `ask` plus `updatedInput` in one reply on codex, which
        can put a question but cannot carry the rewrite that goes with it
cmds:
    sh -c "$DISPATCHER_COMMAND"   # PermissionRequest, the arrival codex asks on
expect: the ticketed question becomes a refusal naming why, and the ticket text
        never reaches codex; a ticket-less question still defers to the native
        prompt
why:    a question asked without its ticket shows the human the ORIGINAL command
        and approves it (HATS-1642) — so the ticket must not go missing in
        silence, and the rule must stay "refuse when the ticket cannot follow"
        rather than "refuse always"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _helpers.sessions import stand_in_session
from _helpers.hook_chain import run_codex_dispatch

pytestmark = [pytest.mark.consent, pytest.mark.surfaces]

SESSION_ID = "sid-codex-ticket"
TICKET = "AI_HATS_CONSENT" + "_TICKET=nonce git push --force"


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _asks(*, with_ticket: bool) -> dict:
    spoken: dict = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": "this needs explicit consent",
    }
    if with_ticket:
        spoken["updatedInput"] = {"command": TICKET}
    return {"hookSpecificOutput": spoken}


@pytest.fixture
def codex_session(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    mirror = cache / "codex-home" / "skills" / "consent" / "hooks"
    hats_dir = project / ".agent" / "ai-hats"

    def build(*, with_ticket: bool) -> SimpleNamespace:
        gate = _script(
            mirror / "consent.sh",
            f"#!/bin/sh\ncat >/dev/null\nprintf '%s' '{json.dumps(_asks(with_ticket=with_ticket))}'\n",
        )
        (cache / "hooks.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "session": {"id": SESSION_ID, "ai_hats_dir": str(hats_dir)},
                    "hooks": {
                        "PreToolUse": [
                            {"matcher": "Bash", "command": str(gate), "tag": "ai-hats:consent"}
                        ]
                    },
                }
            )
        )
        env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="codex")
        env |= {
            "AI_HATS_DIR": str(hats_dir),
            "AI_HATS_SESSION_CACHE_DIR": str(cache),
            "AI_HATS_PROJECT_DIR": str(project),
            "AI_HATS_PYTHON": sys.executable,
        }
        return SimpleNamespace(project=project, env=env)

    return build


def test_a_ticket_that_cannot_follow_becomes_a_refusal_that_says_so(codex_session) -> None:
    """Not a question without its ticket, which would approve the original line."""
    session = codex_session(with_ticket=True)
    done = run_codex_dispatch(
        session.project,
        session.env,
        event="PermissionRequest",
        tool="exec",
        tool_input={"command": "git push --force"},
    )

    spoken = json.loads(done.stdout)["hookSpecificOutput"]
    assert spoken["decision"]["behavior"] == "deny", done.stdout
    assert "cannot carry the consent" in spoken["decision"]["message"]
    assert TICKET not in done.stdout, "the ticket was handed over on a surface that cannot use it"


def test_a_question_with_nothing_to_carry_still_reaches_the_native_prompt(codex_session) -> None:
    """The control that keeps the rule "refuse when the ticket cannot follow"
    from becoming "refuse always": nothing is lost here, so codex asks."""
    session = codex_session(with_ticket=False)
    done = run_codex_dispatch(
        session.project,
        session.env,
        event="PermissionRequest",
        tool="exec",
        tool_input={"command": "git push --force"},
    )

    assert done.stdout.strip() == "", (
        f"a ticket-less question was answered instead of deferred:\n{done.stdout!r}"
    )
