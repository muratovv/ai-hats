"""e2e (HATS-2010)

flow:   a person launches a session from an expression and reads it back
cmds:
    ai-hats -re dry-run "hypothesis-intake + worker" --json
    ai-hats -re agent "hypothesis-intake + worker" --dry-run --json
    ai-hats -re show-prompt "hypothesis-intake + worker"
    ai-hats -re list tokens "hypothesis-intake + worker" --approx
    ai-hats -re "hypothesis-intake + worker"
expect: the agent dry-run and the HITL dry-run carry one composition; show-prompt
        sits inside the framed prompt; list tokens prices that plan; a live
        session writes that prompt, names the marker, arms no hooks, and says so
why:    the projections already printed the plan, and a session that composed
        the expression as a role would arm the wrong gates or refuse in silence
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _helpers.hitl import drive_bare_hitl

pytestmark = [pytest.mark.integration, pytest.mark.library]

EXPR = "hypothesis-intake + worker"


def _prompt_body(full_text: str) -> str:
    match = re.search(r"^prompt {4}\S+\n(.*)\n\nmaterialized\n", full_text, re.S | re.M)
    assert match, full_text[:400]
    return match.group(1)


def test_the_session_projections_agree(tmp_project) -> None:
    from ai_hats.composition_seam import build_preview_payload
    from ai_hats.costs import analyze_plan

    hitl = tmp_project.run("-re", "dry-run", EXPR, "--json").expect_ok()
    agent = tmp_project.run("-re", "agent", EXPR, "--dry-run", "--json").expect_ok()
    assert json.loads(hitl.stdout)["composition"] == json.loads(agent.stdout)["composition"]

    full = tmp_project.run("-re", "dry-run", EXPR, "--full").expect_ok().stdout
    shown = tmp_project.run("-re", "show-prompt", EXPR).expect_ok().stdout
    assert shown.rstrip("\n") in _prompt_body(full)

    plan = build_preview_payload(tmp_project.path, role=EXPR, experimental=True).plan
    total = f"{analyze_plan(plan, exact=False).total_tokens:,}"
    priced = tmp_project.run("-re", "list", "tokens", EXPR, "--approx").expect_ok()
    assert total in priced.stdout


def test_a_live_session_writes_the_plan_and_arms_nothing(tmp_project, requires_claude_auth) -> None:
    # HATS-2010 — the session the projections were waiting for.
    driven = (
        drive_bare_hitl(
            tmp_project,
            subcommand_args=("-re",),
            extra_args=(EXPR,),
            timeout=90,
        )
        .expect_no_hang()
        .expect_exit_in({0, 130})
        .expect_start_banner(role=EXPR, provider="claude")
    )
    assert "candidate session: 0 hooks, 0 consent points" in driven.stdout_plain

    prompts = list(Path(tmp_project.path).glob(".agent/ai-hats/sessions/runs/*/meta_prompt.txt"))
    assert len(prompts) == 1, prompts
    session_dir = prompts[0].parent
    full = tmp_project.run("-re", "dry-run", EXPR, "--full").expect_ok().stdout
    assert prompts[0].read_text(encoding="utf-8") == _prompt_body(full)

    record = json.loads((session_dir / "role_materialization.json").read_text(encoding="utf-8"))
    composition = record["composition"]
    assert composition["identity"] == EXPR
    assert composition["dsl"] == "re"
    assert composition["hooks"] == {"runtime": [], "external": []}
    shown = tmp_project.run("-re", "show-prompt", EXPR).expect_ok().stdout
    assert shown.rstrip("\n") in prompts[0].read_text(encoding="utf-8")

    from ai_hats.check_resolve import resolve_carried_checks
    from ai_hats.session_identity import SessionIdentity

    # The channel rack asks. A non-empty row still resolves in the unit tests;
    # here the session's own marker must make this project arm none.
    identity = SessionIdentity(
        id="probe",
        role=composition["identity"],
        provider=record["provider"],
        project_dir=Path(tmp_project.path),
        session_dir=session_dir,
        dsl=composition["dsl"],
    )
    assert resolve_carried_checks(tmp_project.path, "rack", identity=identity) == ()
