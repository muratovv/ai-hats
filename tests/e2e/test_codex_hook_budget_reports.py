"""e2e (HATS-1858)

flow:   a composed chain of slow gates overruns the budget for one tool call,
        and the operator expects to be told rather than to get silence
cmds:
    sh -c "$DISPATCHER_COMMAND"   # with AI_HATS_HOOK_TIMEOUT_S=1
expect: the dispatcher answers inside the surface's own larger bound, the
        refusal names the variable that widens it, and the chain stops rather
        than waving the remaining gates through
why:    codex bounded the dispatcher and the hook at the same 60 s, so every
        timeout branch was unreachable: the host killed the dispatcher first and
        the tool call met no verdict at all
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

SESSION_ID = "sid-codex-budget"


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def slow_chain(tmp_path: Path) -> SimpleNamespace:
    """Two hooks, each slower than the whole chain's budget."""
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    mirror = cache / "codex-home" / "skills" / "slow" / "hooks"
    ledger = tmp_path / "ledger.txt"
    hats_dir = project / ".agent" / "ai-hats"

    rows = []
    for name in ("first", "second"):
        script = _script(
            mirror / f"{name}.sh",
            f"#!/bin/sh\necho {name} >> '{ledger}'\ncat >/dev/null\nsleep 5\n",
        )
        rows.append({"matcher": "Bash", "command": str(script), "tag": f"ai-hats:{name}"})

    (cache / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session": {"id": SESSION_ID, "ai_hats_dir": str(hats_dir)},
                "hooks": {"PreToolUse": rows},
            }
        )
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="codex")
    env |= {
        "AI_HATS_DIR": str(hats_dir),
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_PYTHON": sys.executable,
        "AI_HATS_HOOK_TIMEOUT_S": "1",
    }
    return SimpleNamespace(project=project, env=env, ledger=ledger)


def test_a_chain_that_overruns_its_budget_still_answers(slow_chain) -> None:
    """A killed dispatcher leaves no verdict; this one is alive to produce it."""
    done = run_codex_dispatch(
        slow_chain.project,
        slow_chain.env,
        tool="exec",
        tool_input={"command": "echo hi"},
        timeout=30,
    )

    spoken = json.loads(done.stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny", done.stdout
    assert "AI_HATS_HOOK_TIMEOUT_S" in spoken["permissionDecisionReason"], (
        f"a budget refusal must name the bound to raise:\n{done.stdout!r}"
    )


def test_one_slow_hook_cannot_let_the_rest_pass_unexamined(slow_chain) -> None:
    """One budget for the call, not one per hook: with nothing left the chain
    refuses rather than waving the remaining gates through."""
    run_codex_dispatch(
        slow_chain.project,
        slow_chain.env,
        tool="exec",
        tool_input={"command": "echo hi"},
        timeout=30,
    )

    fired = slow_chain.ledger.read_text().split() if slow_chain.ledger.is_file() else []
    assert fired == ["first"], f"the chain kept going after its budget was gone: {fired}"


def test_the_surface_bound_is_always_the_larger_of_the_two() -> None:
    """Equal bounds are what made every timeout branch unreachable, and the
    number codex writes into its TOML is derived from this one."""
    from ai_hats.surfaces.codex.runtime_hooks import build_hook_cli_args
    from ai_hats.surfaces.hook_channel import resolve_hook_timeout, surface_timeout

    assert surface_timeout({}) > resolve_hook_timeout({})
    written = {row.split("timeout = ")[1].rstrip(" }]") for row in build_hook_cli_args()[1::2]}
    assert written == {f"{surface_timeout():.0f}"}, written
