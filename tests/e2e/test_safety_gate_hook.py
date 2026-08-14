"""e2e (HATS-1556)

flow:   an agent executing destructive bash commands during tool calls
cmds:
    # inside agent tool call running destructive command
    git push origin master --force
expect: safety gate hook intercepts destructive command and requires explicit user
        confirmation
why: without safety gate hooks, agents execute irreversible destructive shell commands
     without review"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks/safety_gate.py"
)


def _decide(
    command: str,
    *,
    env_extra: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict:
    """Run the hook on a Bash payload; return its decision ({} when it allows)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    env.update(env_extra or {})
    res = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )
    assert res.returncode == 0, res.stderr
    if not res.stdout.strip():
        return {}
    return json.loads(res.stdout)["hookSpecificOutput"]


def _denied(command: str, **kw) -> str:
    out = _decide(command, **kw)
    assert out.get("permissionDecision") == "deny", f"{command!r} was ALLOWED: {out}"
    return out["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf ~",
        "sudo rm -rf /",
        "ls && rm -rf /",
    ],
)
def test_catastrophic_paths_are_denied(command):
    assert "filesystem root" in _denied(command)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf app.db",
        "rm data/users.sqlite3",
        "rm -f dump.sql",
        "rm terraform.tfstate",
        "rm -rf volumes/",
        "rm .env",
        # HATS-1430: recorded experiment runs are gitignored and irreproducible,
        # so the repo cannot restore them — the thing the list is a proxy for.
        "rm -rf experiments/hatrack-hardening/control/runs",
    ],
)
def test_protected_data_is_denied(command):
    assert "protected data" in _denied(command)


def test_in_place_sed_is_denied():
    assert "in place" in _denied("sed -i 's/a/b/' file.py")


def test_destructive_sql_through_a_client_is_denied():
    assert "destructive SQL" in _denied('psql -c "DROP TABLE users"')


def test_a_filesystem_formatter_is_denied():
    assert "formats a filesystem" in _denied("mkfs.ext4 /dev/sda1")


def test_dd_writing_to_a_device_is_denied():
    assert "destroys a disk" in _denied("dd if=/dev/zero of=/dev/sda")


def test_granting_yolo_inline_is_denied():
    assert "cannot be granted inline" in _denied("AI_HATS_YOLO=1 rm -rf app.db")


# A shell wrapper hands its whole script over as ONE token, so every check above
# read it as a single opaque argument and found nothing (HATS-1642 review).
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ('bash -c "AI_HATS_PLAN_ACK=1 rack transition HATS-1 execute"', "granted inline"),
        ("sh -c 'cd . && AI_HATS_MERGE_ACK=1 ai-hats wt merge task/x'", "granted inline"),
        ('bash -lc "AI_HATS_YOLO=1 rm -rf app.db"', "granted inline"),
        ('bash -c "rm -rf /"', "filesystem root"),
        ("sh -c 'sed -i s/a/b/ file.py'", "in place"),
        ('zsh -c "mkfs.ext4 /dev/sda1"', "formats a filesystem"),
    ],
)
def test_what_a_shell_wrapper_hides_is_still_denied(command, expected):
    assert expected in _denied(command)


@pytest.mark.parametrize(
    "command",
    [
        'bash -c "ls -la"',
        "sh -c 'echo rm -rf /'",
        # Not a shell: `-c` is python's own flag and its payload is not a command.
        "python -c \"print('rm -rf /')\"",
    ],
)
def test_a_shell_wrapper_around_something_harmless_still_passes(command):
    """Control: looking inside must not turn every wrapper into a refusal."""
    assert _decide(command) == {}, command


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "rm -rf /tmp/scratch",
        "rm build/artifact.txt",
        "sed 's/a/b/' file.py",
        "echo 'drop table users'",
    ],
)
def test_benign_commands_are_allowed(command):
    """A gate that denies everything is as useless as one that denies nothing."""
    assert _decide(command) == {}


def test_the_ack_opens_protected_data_but_never_the_root():
    ack = {"AI_HATS_DESTRUCTIVE_ACK": "1"}

    assert _decide("rm -rf app.db", env_extra=ack) == {}
    assert "No consent flag overrides this" in _denied("rm -rf /", env_extra=ack)


def test_the_yolo_switch_disables_the_gate():
    """Documented kill switch — pinned so it cannot be removed silently."""
    assert _decide("rm -rf /", env_extra={"AI_HATS_YOLO": "1"}) == {}


# ---------------------------------------------------------------------------
# HATS-1642 — `plan → execute` is a question in chat, not a refusal
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """A git repo — the consent ticket lands in the git dir, beside the journal."""
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    return tmp_path


def test_a_plan_to_execute_transition_asks_the_supervisor(repo):
    out = _decide("rack transition HATS-1 execute", cwd=repo)
    assert out.get("permissionDecision") == "ask", f"the transition did not ask: {out}"
    assert "HATS-1" in out.get("permissionDecisionReason", ""), out


def test_typing_a_consent_ticket_is_denied_like_any_other_self_grant(repo):
    """The guard mints the ticket; an agent writing one is forging the answer."""
    forged = f"AI_HATS_CONSENT_TICKET={'a' * 32} rack transition HATS-1 execute"
    assert "minted by this guard" in _denied(forged, cwd=repo)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # The refusal tells the agent to re-run the command — so every shape it
        # may have typed must raise the question, not go quiet (HATS-1642 review).
        (
            "rack transition HATS-7 execute && rack context HATS-7",
            "AI_HATS_CONSENT_TICKET=%s rack transition HATS-7 execute && rack context HATS-7",
        ),
        (
            "rack ls && rack transition HATS-7 execute",
            "rack ls && AI_HATS_CONSENT_TICKET=%s rack transition HATS-7 execute",
        ),
        (
            "rack transition HATS-7 execute --log 'impl started'",
            "AI_HATS_CONSENT_TICKET=%s rack transition HATS-7 execute --log 'impl started'",
        ),
        (
            "rack transition --tasks-dir /t HATS-7 execute",
            "AI_HATS_CONSENT_TICKET=%s rack transition --tasks-dir /t HATS-7 execute",
        ),
    ],
)
def test_every_shape_the_agent_types_raises_the_question(command, expected, repo):
    out = _decide(command, cwd=repo)
    assert out.get("permissionDecision") == "ask", f"{command!r} went quiet: {out}"
    rewritten = out["updatedInput"]["command"]
    nonce = rewritten.split("AI_HATS_CONSENT_TICKET=", 1)[1].split(" ", 1)[0]
    assert rewritten == expected % nonce, rewritten


def test_the_rewrite_answers_in_the_key_the_surface_spoke_in(repo):
    """agy spells the Bash argument `CommandLine`. Writing `command` back at it
    drops the ticket while the prompt claims the command carries one."""
    payload = {
        "tool_name": "run_command",
        "tool_input": {"CommandLine": "rack transition X execute"},
    }
    res = subprocess.run(  # noqa: S603
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        cwd=str(repo),
        env={k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")},
    )
    out = json.loads(res.stdout)["hookSpecificOutput"]

    assert out["permissionDecision"] == "ask", out
    assert "CommandLine" in out["updatedInput"], out["updatedInput"]
    assert "command" not in out["updatedInput"], "invented a key the surface never sent"
    assert out["updatedInput"]["CommandLine"].startswith("AI_HATS_CONSENT_TICKET=")


def test_a_store_that_cannot_mint_records_why_the_question_vanished(tmp_path):
    """The doctrine of HATS-1373/1407: a gate that stopped acting looks exactly
    like a gate with nothing to do — unless it says so."""
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    (tmp_path / ".git" / "ai-hats").mkdir()
    (tmp_path / ".git" / "ai-hats" / "consent").write_text("not a directory", encoding="utf-8")

    assert _decide("rack transition HATS-1 execute", cwd=tmp_path) == {}
    journal = tmp_path / ".git" / "ai-hats" / "bypasses.jsonl"
    assert journal.is_file(), "the question vanished without a trace"
    assert "HATS-1" in journal.read_text(encoding="utf-8")


def test_the_ticket_lands_in_the_repo_the_rack_call_will_run_in(repo, tmp_path):
    """Resolved from the TARGET, like `backlog_write_gate` next door (HATS-1647).

    Resolving from the hook's own cwd puts the ticket in one repo while `rack`
    looks for it in another: the supervisor clicks and the card does not move.
    """
    from ai_hats_library.hooks import consent_ticket

    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(other), check=True, timeout=30
    )

    out = _decide(f"cd {other} && rack transition HATS-9 execute", cwd=repo)

    nonce = out["updatedInput"]["command"].split("AI_HATS_CONSENT_TICKET=", 1)[1].split(" ", 1)[0]
    argv = ["transition", "HATS-9", "execute"]  # what `rack` will see as sys.argv[1:]
    assert consent_ticket.consume("HATS-9", start=other, nonce=nonce, argv=argv) is True, (
        "the ticket did not land in the repo the transition runs in"
    )


def test_the_ask_hands_the_rack_call_a_ticket_the_rack_side_can_spend(repo):
    """The two halves meet here: the hook mints, the integrator's reader spends.

    The prefix must sit on the `rack` call itself — put in front of the whole
    string it would belong to `cd`, and the rack process would never see it.
    """
    from ai_hats_library.hooks import consent_ticket

    out = _decide("cd sub && rack transition HATS-7 execute", cwd=repo)
    command = out["updatedInput"]["command"]
    assert command.startswith("cd sub && AI_HATS_CONSENT_TICKET="), command
    assert command.endswith(" rack transition HATS-7 execute"), command

    nonce = command.split("AI_HATS_CONSENT_TICKET=", 1)[1].split(" ", 1)[0]
    argv = ["transition", "HATS-7", "execute"]  # what `rack` will see as sys.argv[1:]
    assert consent_ticket.consume("HATS-7", start=repo, nonce=nonce, argv=argv) is True
    # …and the very same ticket opens nothing else, however close (HATS-1642).
    assert consent_ticket.peek("HATS-7", start=repo, nonce=nonce, argv=[*argv, "--json"]) is False


@pytest.mark.parametrize(
    "command",
    [
        "rack context HATS-1",
        "rack ls",
        "rack transition HATS-1 done",
        "rack transition HATS-1 review",
        'rack transition HATS-1 --log "note"',
        # The op flag eats its value, so a message SAYING execute is still a note.
        'rack transition HATS-1 --log "execute"',
        # --force skips the consent gate inside rack; asking would be theatre.
        'rack transition HATS-1 execute --force --reason "manual"',
    ],
)
def test_the_neighbouring_rack_forms_never_prompt(command, repo):
    """Control (green before the ask existed): only `plan → execute` is a question."""
    assert _decide(command, cwd=repo) == {}, command
