"""e2e (HATS-1858)

flow:   an operator runs a codex-provider role whose composition binds a `Bash`
        gate, and expects that gate to fire on codex's own shell
cmds:
    sh -c "$DISPATCHER_COMMAND"   # the string codex puts in its own TOML
expect: a composed chain of two hooks runs in order on `exec`, `shell` and
        `local_shell` alike, and the refusal that reaches codex is the second
        hook's; a permitted command passes with both hooks still having run
why:    `claude_hook_adapter.py` held the string `Bash` zero times, so every
        shipped terminal gate compared literally against `exec` and missed —
        `safety_gate.py`, `pre_bash_shared_state_guard.sh` and
        `tool_call_hygiene_guard.sh` never fired on this surface, and all ten
        dispatcher tests fed it a name codex does not send
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

SESSION_ID = "sid-codex-terminal"
OFF_LIMITS = "rm -rf /"


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def codex_chain(tmp_path: Path) -> SimpleNamespace:
    """A pinned codex session whose PreToolUse chain is two hooks: an audit that
    always allows, then a guard that refuses one command."""
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    mirror = cache / "codex-home" / "skills" / "guard" / "hooks"
    ledger = tmp_path / "ledger.txt"

    audit = _script(mirror / "audit.sh", f"#!/bin/sh\necho audit >> '{ledger}'\ncat >/dev/null\n")
    guard = _script(
        mirror / "guard.sh",
        f"#!/bin/sh\n"
        f"echo guard >> '{ledger}'\n"
        f'case "$(cat)" in\n'
        f"  *'{OFF_LIMITS}'*)\n"
        f"    printf '%s' '{json.dumps(_deny())}'\n"
        f"    exit 0 ;;\n"
        f"esac\n",
    )
    hats_dir = project / ".agent" / "ai-hats"
    (cache / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session": {"id": SESSION_ID, "ai_hats_dir": str(hats_dir)},
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "command": str(audit), "tag": "ai-hats:audit"},
                        {"matcher": "Bash", "command": str(guard), "tag": "ai-hats:guard"},
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
    return SimpleNamespace(project=project, env=env, ledger=ledger)


def _deny() -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{OFF_LIMITS} is off limits",
        }
    }


def _fired(chain: SimpleNamespace) -> list[str]:
    return chain.ledger.read_text().split() if chain.ledger.is_file() else []


@pytest.mark.parametrize("native", ["exec", "shell", "local_shell"])
def test_a_bash_matcher_reaches_the_name_codex_actually_sends(codex_chain, native: str) -> None:
    """`exec` is the name in the operator's own rollout logs; the other two are
    what older and internal builds call the same tool."""
    done = run_codex_dispatch(
        codex_chain.project,
        codex_chain.env,
        tool=native,
        tool_input={"command": OFF_LIMITS},
    )

    spoken = json.loads(done.stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny", done.stdout
    assert OFF_LIMITS in spoken["permissionDecisionReason"]
    assert sorted(_fired(codex_chain)) == ["audit", "guard"], (
        f"a composed gate did not run at all; fired: {_fired(codex_chain)}"
    )


def test_the_second_hook_overrides_the_first(codex_chain) -> None:
    """The whole reason this drives a chain: the audit allowed, and the verdict
    that reached codex is still the guard's."""
    done = run_codex_dispatch(
        codex_chain.project,
        codex_chain.env,
        tool="exec",
        tool_input={"command": OFF_LIMITS},
    )
    assert sorted(_fired(codex_chain)) == ["audit", "guard"]
    # Which of the two DECIDED is the assertion that survived parallelism —
    # they run together now, so the ledger's order stopped being evidence.
    assert (
        OFF_LIMITS in json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    ), done.stdout


def test_the_negative_control_an_allowed_command_passes(codex_chain) -> None:
    """Without this a green run above is indistinguishable from a gate that
    refuses everything."""
    done = run_codex_dispatch(
        codex_chain.project,
        codex_chain.env,
        tool="exec",
        tool_input={"command": "echo hello"},
    )

    assert "permissionDecision" not in done.stdout, done.stdout
    assert sorted(_fired(codex_chain)) == ["audit", "guard"]
