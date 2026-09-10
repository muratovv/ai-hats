"""e2e (HATS-1642)

flow:   an agent asking the supervisor to approve `plan → execute` from chat
cmds:
    rack transition SBX-001 execute
expect: the composed PreToolUse chain returns `ask` plus a one-shot consent ticket,
        and that ticket — spent once, for that card only — is what moves the card
why:    the whole chain, not one hook: a second hook on the Bash matcher can override
        the verdict, and a ticket rack refuses is a question asked for nothing
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git as _git
from _helpers.hook_chain import Verdict, build_session_settings, run_approved, run_chain

pytestmark = [pytest.mark.integration, pytest.mark.consent]

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
#: Minted by `safety_gate.py` when the supervisor answers, spent by the rack's
#: plan-consent gate (HATS-1642). An agent typing one is refused as a self-grant.
TICKET_ENV = "AI_HATS_CONSENT_TICKET"

_PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)


def _rack(project: Path, *args: str, env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603,S607 - session PATH selects the wrapper
        ["rack", *args],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def env(project: Path, checkout_bin: Path) -> dict:
    from _helpers.env import checkout_pythonpath
    from _helpers.sessions import stand_in_wrapped_session

    e = os.environ.copy()
    e.pop("AI_HATS_PLAN_ACK", None)
    e.pop(TICKET_ENV, None)
    e["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, e.get("PYTHONPATH", ""))
    e["AI_HATS_ROOT_PID"] = str(os.getpid())
    e["PATH"] = os.pathsep.join([str(checkout_bin), e.get("PATH", "")])
    return stand_in_wrapped_session(e, project, "e2e-plan-consent-ticket")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    (root / TASKS_SUB).mkdir(parents=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")
    return root


@pytest.fixture
def planned(project: Path, env: dict):
    """Two cards sitting in ``plan`` with a filled plan.md — the gate's doorstep."""

    def _make(title: str) -> str:
        created = _rack(project, "create", title, "--role", "assistant", env=env)
        assert created.returncode == 0, created.stderr
        task_id = next(w for w in created.stdout.split() if w.startswith("SBX-"))
        assert _rack(project, "transition", task_id, "plan", env=env).returncode == 0
        plan_md = project / TASKS_SUB / task_id / "plan.md"
        plan_md.write_text(plan_md.read_text(encoding="utf-8") + _PLAN_SECTIONS, encoding="utf-8")
        return task_id

    return _make


@pytest.fixture
def settings(project: Path) -> Path:
    return build_session_settings(project)


def _ticket_from(command: str) -> str:
    return command.split(f"{TICKET_ENV}=", 1)[1].split(" ", 1)[0]


def _ask_for(project, settings, env, task_id: str, command: str = "") -> Verdict:
    """The chain's `ask` for ``command`` (default: the plain `→ execute` move)."""
    verdict = run_chain(
        project, command or f"rack transition {task_id} execute", settings=settings, env=env
    )
    assert verdict.decision == "ask", f"the chain did not ask: {verdict}"
    assert verdict.updated_input, f"the ask carried no rewritten command: {verdict}"
    return verdict


def _state_of(project, env, task_id: str) -> str:
    """Where the card actually sits — the only proof a shell run really moved it.

    A rewritten command can carry a redirect or a pipe, so its exit code belongs
    to the tail, not to `rack`. The card is what settles it (HATS-1682 T2).
    """
    seen = _rack(project, "context", task_id, env=env)
    assert seen.returncode == 0, seen.stderr
    for line in seen.stdout.splitlines():
        if line.strip().startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"`rack context {task_id}` named no state:\n{seen.stdout}")


def test_the_chain_turns_plan_to_execute_into_a_question(project, settings, env, planned):
    task_id = planned("ticket probe")
    verdict = run_chain(project, f"rack transition {task_id} execute", settings=settings, env=env)

    assert verdict.decision == "ask", f"the chain did not ask: {verdict}"
    assert verdict.hook == "safety_gate.py", f"another hook answered: {verdict}"
    assert task_id in verdict.reason, verdict.reason
    assert verdict.updated_input["command"].startswith(f"{TICKET_ENV}="), verdict.updated_input


def test_the_ticket_the_chain_minted_is_what_moves_the_card(project, settings, env, planned):
    """Run what was approved, not a reconstruction of it (HATS-1682 T2)."""
    task_id = planned("ticket probe")
    verdict = _ask_for(project, settings, env, task_id)

    moved = run_approved(project, verdict, env=env)

    assert moved.returncode == 0, moved
    assert "→ execute" in moved.stdout, moved
    assert _state_of(project, env, task_id) == "execute"


def test_the_ticket_opens_the_command_it_was_asked_about_and_no_other(
    project, settings, env, planned
):
    """Same card, same session, different call — and the answer does not carry.

    The mint precedes the answer, so a "No" always leaves a live ticket behind.
    Binding it to the invocation is what stops that leftover opening the next
    call inside its window (HATS-1642, live probe).
    """
    task_id = planned("command binding")
    verdict = _ask_for(project, settings, env, task_id)
    nonce = _ticket_from(verdict.updated_input["command"])

    # A DIFFERENT argv, same nonce — the one shape `run_approved` cannot spell,
    # because the guard never approved this call.
    other = _rack(
        project, "transition", task_id, "execute", "--json", env={**env, TICKET_ENV: nonce}
    )
    assert other.returncode != 0, f"a ticket for another call passed:\n{other.stdout}"
    assert "supervisor approval" in other.stdout + other.stderr

    asked = run_approved(project, verdict, env=env)
    assert asked.returncode == 0, f"the call it WAS asked about was refused:\n{asked}"


def test_a_transition_that_fails_downstream_still_spends_the_ticket(
    project, settings, env, planned
):
    """The wrapper consumes one-use authorization before starting the tool."""
    task_id = planned("downstream failure")
    verdict = _ask_for(project, settings, env, task_id)
    # A FILE where the worktree bookkeeping wants a directory: `→ execute` gets
    # past consent and dies further down, exactly as the ordering worry describes.
    worktrees = project / ".agent" / "ai-hats" / "sessions" / "worktrees"
    worktrees.parent.mkdir(parents=True, exist_ok=True)
    worktrees.write_text("not a directory", encoding="utf-8")

    failed = run_approved(project, verdict, env=env)
    assert failed.returncode != 0, f"expected the transition to fail:\n{failed}"

    worktrees.unlink()
    retried = run_approved(project, verdict, env=env)

    assert retried.returncode != 0, f"a spent ticket was replayed:\n{retried}"
    assert "ticket already spent or never issued" in retried.output


def test_a_spent_ticket_does_not_open_the_gate_twice(project, settings, env, planned):
    """One card, one ticket, two attempts — nothing but the ticket differs."""
    task_id = planned("replay")
    verdict = _ask_for(project, settings, env, task_id)
    assert run_approved(project, verdict, env=env).returncode == 0
    # Park the card back in plan: the slot is free and the gate is armed again.
    assert _rack(project, "transition", task_id, "blocked", env=env).returncode == 0
    assert _rack(project, "transition", task_id, "plan", env=env).returncode == 0

    replayed = run_approved(project, verdict, env=env)

    assert replayed.returncode != 0, f"a spent ticket was accepted again:\n{replayed}"
    assert "supervisor approval" in replayed.output


def test_a_ticket_belongs_to_the_card_it_was_asked_about(project, settings, env, planned):
    asked, other = planned("asked about"), planned("not asked about")
    verdict = _ask_for(project, settings, env, asked)
    nonce = _ticket_from(verdict.updated_input["command"])

    borrowed = _rack(project, "transition", other, "execute", env={**env, TICKET_ENV: nonce})

    assert borrowed.returncode != 0, f"another card's ticket passed:\n{borrowed.stdout}"
    # …and the refusal did not eat the consent the supervisor gave to `asked`.
    kept = run_approved(project, verdict, env=env)
    assert kept.returncode == 0, kept


def test_an_invented_ticket_moves_nothing(project, env, planned):
    task_id = planned("forged")

    forged = _rack(project, "transition", task_id, "execute", env={**env, TICKET_ENV: "f" * 32})

    assert forged.returncode != 0, f"an invented ticket passed:\n{forged.stdout}"


def test_the_chain_refuses_a_ticket_the_agent_typed_itself(project, settings, env, planned):
    """Control on the other side: the guard mints tickets, the agent does not."""
    task_id = planned("typed")
    verdict = run_chain(
        project,
        f"{TICKET_ENV}={'a' * 32} rack transition {task_id} execute",
        settings=settings,
        env=env,
    )

    assert verdict.denied, f"a typed ticket was not refused: {verdict}"
    assert TICKET_ENV in verdict.reason, verdict.reason


def test_the_established_yolo_self_grant_is_still_refused(project, settings, env):
    """Positive control: a chain that refuses everything looks the same as a
    chain that gained one rule — this tells them apart."""
    verdict = run_chain(project, "AI_HATS_YOLO=1 rm -rf /tmp/x", settings=settings, env=env)
    assert verdict.denied, f"the pre-existing YOLO self-grant was ALLOWED: {verdict}"


def test_the_environment_channel_asks_no_question(project, settings, env, planned):
    """Headless, cron and hookless surfaces cannot answer a prompt — where the
    supervisor pre-approved in the environment, the chain must stay silent."""
    task_id = planned("pre-approved")
    verdict = run_chain(
        project,
        f"rack transition {task_id} execute",
        settings=settings,
        env=env,
        ack="AI_HATS_PLAN_ACK",
    )
    assert not verdict.gated, f"a pre-approved transition was gated: {verdict}"


@pytest.mark.parametrize(
    "form",
    [
        "rack ls",
        "rack context {task_id}",
        "rack transition {task_id} --log 'note'",
        "cd . && rack transition {task_id} --log 'note'",
        "env FOO=1 rack transition {task_id} --log 'note'",
    ],
)
def test_the_neighbouring_rack_forms_pass_through_the_chain(project, settings, env, planned, form):
    task_id = planned("neighbour")
    verdict = run_chain(project, form.format(task_id=task_id), settings=settings, env=env)
    assert not verdict.gated, f"{form} was gated: {verdict}"
    assert verdict.hook == "safety_gate.py", f"{form} still needs an allow-rule: {verdict}"


def test_the_chain_asks_on_the_edge_that_merges_into_master(project, settings, env, planned):
    """`→ done` carries the worktree merge into master, and used to be answered
    by SILENCE — which the ordinary permission flow read as allow, merging a
    branch with no question asked at all (HATS-1682, measured on HATS-1681).
    The role declares consent on this edge now, so the guard asks on it."""
    task_id = planned("into master")
    verdict = run_chain(project, f"rack transition {task_id} done", settings=settings, env=env)

    assert verdict.decision == "ask", f"the edge into master was not gated: {verdict}"
    assert verdict.hook == "safety_gate.py", f"another hook answered: {verdict}"
    assert verdict.updated_input["command"].startswith(f"{TICKET_ENV}="), verdict.updated_input


def test_the_edge_into_master_refuses_without_consent_and_moves_with_it(
    project, settings, env, planned
):
    """The whole loop on the edge the live probe walked straight through.

    Before HATS-1682 nothing gated `review → done`: the guard was silent and the
    rack declared no consent handler on it, so a bare command merged a branch
    into master and reported success. Now the role declares the edge, the engine
    refuses it unanswered, and the guard's own ticket is what opens it.
    """
    task_id = planned("into master")
    walk = {**env, "AI_HATS_CONSENT_ACK": "1"}  # only the LAST edge is under test
    for state in ("execute", "document", "review"):
        assert _rack(project, "transition", task_id, state, env=walk).returncode == 0

    refused = _rack(project, "transition", task_id, "done", env=env)
    assert refused.returncode != 0, f"the edge into master was not gated:\n{refused.stdout}"
    assert "requires supervisor approval" in refused.stdout + refused.stderr

    verdict = _ask_for(project, settings, env, task_id, f"rack transition {task_id} done")
    moved = run_approved(project, verdict, env=env)

    assert moved.returncode == 0, f"the answered edge was still refused:\n{moved}"
    assert "→ done" in moved.stdout, moved
    assert _state_of(project, env, task_id) == "done"


def test_the_chain_does_not_wave_through_what_it_must_not(project, settings, env):
    """A verb with no declaration behind it is neither asked about nor allowed —
    the ordinary permission flow decides, and the guard says nothing."""
    verdict = run_chain(project, "rack create 'x'", settings=settings, env=env)
    assert verdict.hook == "", f"rack create was auto-approved by {verdict.hook}"


def test_an_allow_rule_no_longer_silences_the_question(project, settings, env, planned):
    """ADR-0031 D1 — the hook holds the gate, whatever `permissions.allow` says.

    This arm holds the HOOK half only: the harness half (that Claude Code does
    not let an allow-rule override a hook's `ask`) is a claim about someone
    else's release and is measured by
    `experiments/harness-permission-precedence/probe.sh`.
    """
    claude = project / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(rack transition *)"]}}, indent=2)
    )
    task_id = planned("allow-rule probe")

    verdict = run_chain(
        project,
        f"rack transition {task_id} execute",
        settings=settings,
        env={**env, "HOME": str(project)},
    )

    assert verdict.decision == "ask", f"an allow-rule reached the hook: {verdict}"
    assert verdict.context == "", f"the retired lint still speaks: {verdict.context}"


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "cd . && ",
        "env FOO=1 ",
        # HATS-1682: the maintainer injection prescribes `timeout` for anything
        # that can hang, and the gate used to read the wrapper as the binary —
        # so the everyday spelling of a gated move asked nothing at all.
        "timeout 180 ",
        "nice -n 10 ",
    ],
)
def test_the_question_survives_the_shapes_the_agent_actually_types(
    project, settings, env, planned, prefix
):
    """`cd x && rack …` and `env FOO=1 rack …` are the everyday spellings; the
    ticket must land on the rack call in each, or the rack process never sees it.

    Run through a real shell, because the wrapper is the whole point: `timeout`
    and `nice` do not understand `VAR=VAL`, so a ticket placed after them exits
    127 and moves nothing. Lifting the nonce into `env` hid that (HATS-1682 A2).
    """
    task_id = planned("shape")
    verdict = _ask_for(
        project, settings, env, task_id, f"{prefix}rack transition {task_id} execute"
    )

    moved = run_approved(project, verdict, env=env)

    assert moved.returncode == 0, f"{prefix!r} approved a command the shell cannot run:\n{moved}"
    assert _state_of(project, env, task_id) == "execute"


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(" 2>&1", id="stderr-to-stdout"),
        pytest.param(" 2>&1; echo rc=$?", id="redirect-and-tail"),
        pytest.param(" > /dev/null", id="stdout-to-file"),
        pytest.param(" 2>&1 | tail -5", id="redirect-and-pipe"),
    ],
)
def test_what_the_shell_eats_does_not_change_the_command_that_was_approved(
    project, settings, env, planned, suffix
):
    """A redirection never reaches the child's argv, but the lexer hands it over
    as tokens — `2>&1` arrives as `2`, `>&`, `1`. Binding the ticket to those
    made `rack` compute a different argv and refuse a click already given, so the
    supervisor paid for a transition that never happened (HATS-1682, live probe).
    """
    task_id = planned("shell-owned tail")
    verdict = _ask_for(
        project, settings, env, task_id, f"rack transition {task_id} execute{suffix}"
    )

    moved = run_approved(project, verdict, env=env)

    # The card, not the exit code: with `| tail` or `; echo` the rc belongs to
    # the tail, so only the card can say whether the click bought anything.
    assert _state_of(project, env, task_id) == "execute", (
        f"the click was spent on nothing:\n{moved}"
    )
