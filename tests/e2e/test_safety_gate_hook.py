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
    assert consent_ticket.consume("HATS-7", start=repo, nonce=nonce) is True


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
