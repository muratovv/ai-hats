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

import atexit
import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks/safety_gate.py"
)


#: Where :func:`_plant_session` puts the envelope's on-disk half, inside the
#: probe repo so one fixture owns both.
SESSION_DIRNAME = ".session"


def _plant_session(repo: Path, *targets: str, wt: bool = False) -> None:
    """Make ``repo`` look like a session whose role declared consent (HATS-1682).

    WHERE the guard asks is the role's declaration, read from
    ``<session_dir>/role_materialization.json`` — so a probe with no session
    declares nothing and is asked nothing, which is correct and useless here.
    """
    session_dir = repo / SESSION_DIRNAME
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "role_materialization.json").write_text(
        json.dumps(
            {
                "consent": [
                    # The envelope carries the ends ALREADY PARSED (HATS-1719):
                    # the guard is stdlib-only and cannot import the grammar, so
                    # it reads `to` rather than cutting `selector` itself.
                    *(
                        {
                            "app": "consent_gate",
                            "path": ["rack.transition"],
                            "selector": f"x->{state}",
                            "from": "x",
                            "to": state,
                        }
                        for state in targets
                    ),
                    *(
                        [
                            {
                                "app": "consent_gate",
                                "path": ["wt.merge"],
                                "selector": "pre-merge",
                                "from": None,
                                "to": None,
                            }
                        ]
                        if wt
                        else []
                    ),
                ]
            }
        ),
        encoding="utf-8",
    )


@functools.lru_cache(maxsize=1)
def _neutral_root() -> Path:
    """A cwd and HOME this file owns.

    The hook's permission lint scans `(Path.cwd(), Path.home())`
    (`consent_permission_lint.py:138`), so an inherited pair makes every
    `== {}` here an assertion about someone else's `.claude/settings*.json` —
    red in the main checkout the moment a session accepts an allow-rule, green
    in a worktree that has none. Found by the HATS-1716 audit, measured both ways.
    """
    root = Path(tempfile.mkdtemp(prefix="ai-hats-safety-gate-neutral-"))
    # Not a `tmp_path`: the callers are plain functions, not fixtures. Reaped at
    # exit so the tier does not leave one dir per xdist worker per run behind.
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return root


def _decide(
    command: str,
    *,
    base_env: dict[str, str] | None = None,
    env_extra: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict:
    """Run the hook on a Bash payload; return its decision ({} when it allows)."""
    source_env = os.environ if base_env is None else base_env
    env = {k: v for k, v in source_env.items() if not k.startswith("AI_HATS_")}
    env["HOME"] = str(_neutral_root())
    planted = None if cwd is None else Path(cwd) / SESSION_DIRNAME
    if planted is not None and (planted / "role_materialization.json").is_file():
        env["AI_HATS_SESSION_IDENTITY"] = json.dumps({"v": 1, "session_dir": str(planted)})
    env.update(env_extra or {})
    res = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        cwd=str(cwd) if cwd is not None else str(_neutral_root()),
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


def test_default_probe_ignores_ambient_permission_settings(tmp_path):
    _with_allow(tmp_path, ["Bash(ai-hats:*)"])

    out = _decide("ls -la", base_env={**os.environ, "HOME": str(tmp_path)})

    assert out == {}


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
    """A git repo — the consent ticket lands in the git dir, beside the journal.

    Carries a planted session too: since HATS-1682 the guard asks where the
    ROLE declared consent, so a probe with no declaration is asked nothing.
    """
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    _plant_session(tmp_path, "execute", "done")
    return tmp_path


def test_a_plan_to_execute_transition_asks_the_supervisor(repo):
    out = _decide("rack transition HATS-1 execute", cwd=repo)
    assert out.get("permissionDecision") == "ask", f"the transition did not ask: {out}"
    assert "HATS-1" in out.get("permissionDecisionReason", ""), out


# The hook, not permissions.allow, decides the routine rack calls (HATS-1642):
# a rule wide enough to spare a click on every work-log entry is wide enough to
# swallow `plan → execute`. Authority belongs where the EDGE is visible.


@pytest.mark.parametrize(
    "command",
    [
        'rack transition HATS-1 --log "impl started"',
        "rack transition HATS-1 --set role=implementer",
        "rack transition HATS-1 --attach /tmp/notes.md",
        "rack transition HATS-1 --link related:HATS-2",
        'rack transition HATS-1 --log "a" --set priority=high',
        "rack context HATS-1",
        "rack ls",
        "cd sub && rack transition HATS-1 --log 'from a worktree'",
    ],
)
def test_the_routine_rack_ops_are_allowed_by_the_hook(command, repo):
    out = _decide(command, cwd=repo)
    assert out.get("permissionDecision") == "allow", f"{command!r} was not allowed: {out}"


@pytest.mark.parametrize(
    "command",
    [
        # Carries the worktree teardown-merge into master: a shared-state write,
        # and `rule_pause_before_shared_state_write` wants the pause HERE.
        "rack transition HATS-1 done",
        'rack transition HATS-1 --log "closing" done',
        # Relaxes the FSM arrow — by definition not routine.
        'rack transition HATS-1 execute --force --reason "manual"',
        'rack transition HATS-1 --force --reason "manual" --log "x"',
        # Not enumerated as safe: the list is positive, so a new flag is silence.
        "rack transition HATS-1 --unlink related:HATS-2",
        "rack transition HATS-1 review",
        "rack create 'new card'",
        # A chain is only as allowable as its least allowable link.
        'rack transition HATS-1 --log "x" && git push origin master',
    ],
)
def test_what_the_hook_must_not_wave_through_stays_silent(command, repo):
    """Silence, not allow: these go back to the ordinary permission flow."""
    out = _decide(command, cwd=repo)
    assert out.get("permissionDecision") != "allow", f"{command!r} was auto-approved: {out}"


def _with_allow(repo, rules) -> None:
    (repo / ".claude").mkdir(exist_ok=True)
    (repo / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": rules}}, indent=2), encoding="utf-8"
    )


def test_a_rule_that_silences_a_guard_is_reported_without_blocking(repo):
    """A bound check cannot resolve from a worktree (ADR-0019 D9 clause 4), so
    the warning rides the hook — non-gating, on additionalContext."""
    _with_allow(repo, ["Bash(rack transition *)", "Bash(ai-hats:*)"])

    out = _decide("ls -la", cwd=repo, env_extra={"HOME": str(repo)})

    said = out.get("additionalContext", "")
    assert "rack transition" in said and "ai-hats:*" in said, out
    assert "merge into master" in said, "the ai-hats rule silences the merge pause too"
    assert "settings.local.json" in said, said
    assert out.get("permissionDecision") is None, f"a lint must not gate: {out}"


def test_the_warning_is_said_once_per_session(repo):
    _with_allow(repo, ["Bash(rack transition *)"])
    env = {"HOME": str(repo), "AI_HATS_SESSION_ID": "sid-1"}

    assert _decide("ls -la", cwd=repo, env_extra=env).get("additionalContext"), "never said"
    assert _decide("ls -la", cwd=repo, env_extra=env) == {}, "said twice in one session"
    other = {**env, "AI_HATS_SESSION_ID": "sid-2"}
    assert _decide("ls -la", cwd=repo, env_extra=other).get("additionalContext"), "not re-said"


def test_a_clean_allow_list_draws_no_comment(repo):
    """Fail-under-revert: without this, a lint that warns always would pass."""
    _with_allow(repo, ["Bash(rack context *)", "Bash(git status:*)"])

    assert _decide("ls -la", cwd=repo, env_extra={"HOME": str(repo)}) == {}


def test_the_consent_question_does_not_depend_on_the_allow_list(repo):
    """The whole point of the move: no settings file is consulted to decide it."""
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": []}}), encoding="utf-8"
    )

    out = _decide("rack transition HATS-1 execute", cwd=repo)
    assert out.get("permissionDecision") == "ask", out


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
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")},
            "AI_HATS_SESSION_IDENTITY": json.dumps(
                {"v": 1, "session_dir": str(repo / SESSION_DIRNAME)}
            ),
        },
    )
    out = json.loads(res.stdout)["hookSpecificOutput"]

    assert out["permissionDecision"] == "ask", out
    assert "CommandLine" in out["updatedInput"], out["updatedInput"]
    assert "command" not in out["updatedInput"], "invented a key the surface never sent"
    assert out["updatedInput"]["CommandLine"].startswith("AI_HATS_CONSENT_TICKET=")


def test_a_store_that_cannot_mint_refuses_and_records_why(tmp_path):
    """The doctrine of HATS-1373/1407: a gate that stopped acting looks exactly
    like a gate with nothing to do — unless it says so.

    Since HATS-1682 it says so twice: to the journal, and to the agent, whose
    command is refused. The point was DECLARED, and letting a declared point
    fall back to the ordinary permission flow is how it simply ran.
    """
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    (tmp_path / ".git" / "ai-hats").mkdir()
    (tmp_path / ".git" / "ai-hats" / "consent").write_text("not a directory", encoding="utf-8")
    _plant_session(tmp_path, "execute")

    assert "ticket store" in _denied("rack transition HATS-1 execute", cwd=tmp_path)
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
        # `done` is NOT here since HATS-1682 — the role declares consent on that
        # edge, and its silence was the merge into master nobody was asked about.
        "rack transition HATS-1 review",
        'rack transition HATS-1 --log "note"',
        # The op flag eats its value, so a message SAYING execute is still a note.
        'rack transition HATS-1 --log "execute"',
        # `… execute --force --reason "manual"` left this list at HATS-1682: it
        # asserted that a flag on the command line switches the question off,
        # and consent is not a property of the command — `consent | op --force`.
        # Its replacement is the parametrized principle in
        # tests/e2e/test_consent_force_chain.py, driven through the whole chain.
    ],
)
def test_the_neighbouring_rack_forms_never_prompt(command, repo):
    """Control (green before the ask existed): only `plan → execute` is a question.

    Not a claim of silence — some of these the hook now allows outright, which
    is also not a prompt. What must never happen is `ask` or `deny`.
    """
    decision = _decide(command, cwd=repo).get("permissionDecision")
    assert decision not in ("ask", "deny"), f"{command!r} was gated: {decision}"


def test_a_direct_merge_into_master_asks_where_the_role_declared_it(tmp_path):
    """The other road into master (HATS-1130). `rack transition X done` merges
    through the FSM; `ai-hats wt merge` does it directly, and a gate holding
    only one of them is the asymmetry that started that epic. One declaration
    covers both because it names points of two applications (HATS-1682)."""
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    _plant_session(tmp_path, "execute", wt=True)

    out = _decide("ai-hats wt merge task/hats-1", cwd=tmp_path)

    assert out.get("permissionDecision") == "ask", f"the merge went unasked: {out}"
    assert "task/hats-1" in out["permissionDecisionReason"], out
    assert out["updatedInput"]["command"].startswith("AI_HATS_CONSENT_TICKET="), out


def test_a_role_that_declared_no_merge_point_is_not_asked(tmp_path):
    """Control: the declaration is what decides, not the command's shape."""
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    _plant_session(tmp_path, "execute")  # rack only — no wt point

    assert _decide("ai-hats wt merge task/hats-1", cwd=tmp_path) == {}


def test_command_p_cannot_bypass_the_session_wrapper(tmp_path):
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    _plant_session(tmp_path, "done")

    reason = _denied("command -p rack transition HATS-1 done", cwd=tmp_path)

    assert "session wrapper" in reason


@pytest.mark.parametrize(
    "command",
    [
        "PATH=/usr/bin:/bin rack transition HATS-1 done",
        "env -i rack transition HATS-1 done",
        "env --unset=PATH rack transition HATS-1 done",
    ],
)
def test_path_changing_prefix_cannot_bypass_the_session_wrapper(tmp_path, command):
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    _plant_session(tmp_path, "done")

    reason = _denied(command, cwd=tmp_path)

    assert "session wrapper" in reason


def test_env_with_an_unrelated_variable_still_reaches_the_wrapper(tmp_path):
    subprocess.run(  # noqa: S603,S607 - literal argv, git from PATH
        ["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=30
    )
    _plant_session(tmp_path, "done")

    verdict = _decide("env KEEP=yes rack transition HATS-1 done", cwd=tmp_path)

    assert verdict["permissionDecision"] == "ask"
