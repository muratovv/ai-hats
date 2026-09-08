"""e2e (HATS-632)

flow:   an agent executing shell commands covered by dedicated tools
cmds:
    grep foo .
expect: the PreToolUse hook emits additionalContext suggesting dedicated tools without
        blocking or modifying command permissions
why:    tool hygiene guidance encourages efficient tool choices while remaining
        non-blocking to preserve execution flow
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ai_hats.constants import HOOK_PRE_TOOL_USE

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUARD = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/tool-call-hygiene/hooks/tool_call_hygiene_guard.sh"
)


@pytest.fixture
def _run(hook_repo):
    def run(
        command: str | None,
        *,
        env: dict | None = None,
        raw: str | None = None,
        background: bool = False,
    ):
        if raw is not None:
            stdin = raw
        elif command is None:
            stdin = ""
        else:
            tool_input: dict = {"command": command}
            if background:
                tool_input["run_in_background"] = True
            stdin = json.dumps(
                {
                    "hook_event_name": HOOK_PRE_TOOL_USE,
                    "tool_name": "Bash",
                    "tool_input": tool_input,
                }
            )
        base_env = os.environ.copy()
        base_env.pop("AI_HATS_TOOL_HYGIENE_OFF", None)
        if env:
            base_env.update(env)
        return subprocess.run(
            ["bash", str(GUARD)],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=5,
            env=base_env,
            cwd=hook_repo,
        )

    return run


def _nudge(res) -> str | None:
    """Parse stdout as the hook JSON; return additionalContext text or None."""
    out = res.stdout.strip()
    if not out:
        return None
    data = json.loads(out)
    return data.get("hookSpecificOutput", {}).get("additionalContext")


@pytest.mark.integration
def test_pure_grep_nudges_to_grep_tool(_run):
    res = _run("grep foo .")
    assert res.returncode == 0, res.stderr
    ctx = _nudge(res)
    assert ctx is not None, f"expected a nudge, got stdout={res.stdout!r}"
    assert "Grep" in ctx
    # Non-blocking contract: never emit a permissionDecision.
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "cat a.txt | grep foo",  # covered leading token, but piped
        "grep foo . && echo done",  # chained with &&
        "find . -name x ; ls",  # chained with ;
        "grep foo $(ls)",  # command substitution
        "cat `which bash`",  # backtick subshell
        "grep foo file > out.txt",  # redirection
        "git log --oneline | head",  # non-covered leading token + pipe
        "make build",  # build command, not covered
    ],
)
def test_compound_or_noncovered_gets_no_nudge(_run, command):
    """Bias-to-allow: anything compound/redirected is legitimately Bash."""
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"unexpected nudge for {command!r}: {res.stdout!r}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "command,tool",
    [
        ("ls -R", "Glob"),  # recursive listing
        ("ls -laR /tmp", "Glob"),  # -R inside a flag cluster
        ("sed -i 's/a/b/' f.txt", "Edit"),  # in-place edit
        ("sed -i.bak s/a/b/ f.txt", "Edit"),  # in-place with backup suffix
        ("awk -i inplace '{print}' f.txt", "Edit"),
    ],
)
def test_conditional_covered_forms_nudge(_run, command, tool):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    ctx = _nudge(res)
    assert ctx is not None and tool in ctx, f"{command!r} -> {res.stdout!r}"
    # Mutating commands (sed -i / awk -i) must NEVER be auto-approved.
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "ls -la",  # non-recursive listing is fine
        "sed 's/a/b/' f.txt",  # stream sed (not in-place) is fine
        "awk '{print $1}' f.txt",  # stream awk is fine
    ],
)
def test_noncovered_command_forms_get_no_nudge(_run, command):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"unexpected nudge: {res.stdout!r}"


# --- HATS-1436: exit code masking tests -------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/ | tail",
        "pytest tests/ | head -n 10",
        "pytest tests/ | grep FAILED",
        "pytest tests/ | tee /tmp/gate.log",
        'pytest tests/ > /tmp/gate.log 2>&1; echo "EXIT=$?"',
        "pytest tests/ ; true",
        'pytest tests/ || echo "failed"',
        "pytest tests/ || true",
        "ruff check src/ | tail",
        "make test | grep Error",
        "gates.sh | tail",
        "npm test | head",
        'python -m pytest tests/ > /tmp/gate.log 2>&1; echo "EXIT=$?"',
        # bash-only spelling with no bash in sight: in the tool's zsh this is
        # `exit ""` -> 0 for every run, so it masks rather than preserves.
        "pytest tests/ | tail; exit ${PIPESTATUS[0]}",
        # `pipefail` fixes WHICH status a pipeline reports and says nothing about
        # what a `;` runs next, so it cannot excuse a trailing echo (HATS-1709).
        # This is the measured incident shape: the shell-level defence was there
        # and the status was still the echo's.
        "bash -c 'set -o pipefail; pytest tests/' ; echo done",
        # ...and the exemption was a substring test, so a filename disarmed it.
        "pytest tests/ --junit=pipefail.xml; echo done",
        # A runner named with its PATH — the form this project's own quality-gate
        # skill prescribes, and the form both measured incidents were written in.
        # The whole test corpus used bare names, so nothing here ever exercised
        # what an agent actually types (HATS-1709).
        "bash scripts/gates.sh run unit | tail",
        "bash scripts/ci-local.sh e2e; echo done",
    ],
)
def test_exit_code_masking_nudges(_run, command):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    ctx = _nudge(res)
    assert ctx is not None, f"expected exit code masking nudge for {command!r}, got {res.stdout!r}"
    assert "exit code masking detected" in ctx
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/ && true",
        "set -o pipefail; pytest tests/ | tail",
        # The zsh spelling — the one that preserves the status in the shell the
        # Bash tool runs (HATS-1798). Its bash-only twin is nudged instead.
        "pytest tests/ | tail; exit ${pipestatus[1]}",
        # ...unless an explicit bash runs it, where PIPESTATUS is the right name.
        "bash -c 'pytest tests/ | tail; exit ${PIPESTATUS[0]}'",
        "pytest tests/",
        "python -m pytest tests/",
        # The rule's default shape: redirect only. The harness reports the
        # runner's own exit code, so no status file is involved (HATS-1798).
        "pytest tests/ > /tmp/gate.log 2>&1",
        # The rule's reserved shape, for a verdict that must outlive the call:
        # the leading rm -f is what proves the file belongs to THIS run.
        "rm -f /tmp/gate.rc; pytest tests/ > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc",
        # Capturing the status to a file does not MASK it, at any path — so the
        # guard stays silent here even without the rm -f. Freshness is the
        # rule's business, not this hook's; nudging these taught the agent to
        # distrust capture itself.
        "pytest tests/ > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc",
        "python -m pytest tests/ -q > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc",
        "ruff check src/ > /tmp/lint.log 2>&1; echo $?>/tmp/lint.rc",
    ],
)
def test_exit_code_preservation_gets_no_nudge(_run, command):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"unexpected nudge for {command!r}: {res.stdout!r}"


# --- fail-safe edge cases ----------------------------------------------------


@pytest.mark.integration
def test_empty_payload_no_crash_no_nudge(_run):
    res = _run(None)  # empty stdin (harness no-op / manual test)
    assert res.returncode == 0
    assert _nudge(res) is None


@pytest.mark.integration
def test_garbage_payload_fails_safe(_run):
    res = _run(None, raw="this is not json {{{")
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None


@pytest.mark.integration
def test_non_bash_payload_gets_no_nudge(_run):
    raw = json.dumps({"tool_input": {"file_path": "/tmp/x"}})
    res = _run(None, raw=raw)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None


@pytest.mark.integration
def test_kill_switch_disables_hook(_run):
    res = _run("grep foo .", env={"AI_HATS_TOOL_HYGIENE_OFF": "1"})
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None


# --- HATS-1819: a name in an argument is not a call; `;` does not gate --------


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        # The shape that made the guard cry wolf five times in one session: a
        # work_log entry REPORTING a test run, piped to tail. Recording that the
        # discipline was followed must not read as breaking it.
        'rack transition ID --log "V4: pytest -m integration x.py rc=0, 31 passed" 2>&1 | tail -3',
        'echo "run pytest later" | tee note.txt',
        "grep -nE 'runner|pytest|make' guard.sh | head -5",
        'git commit -m "make the gate green" | tail -1',
    ],
)
def test_runner_name_inside_an_argument_is_not_a_call(_run, command):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"spurious nudge for {command!r}: {res.stdout!r}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/ ; git commit -m wip",
        "make done-gate ; git push origin master",
        "ruff check src/ ; git add -A",
        # Masking and sequencing at once: the mutation is the worse of the two,
        # so it is what the agent is told about.
        "pytest tests/ | tail -5 ; git commit -m wip",
        # `set -o pipefail` fixes WHOSE status you read, never whether the next
        # command honours it — so it must not excuse this.
        "set -o pipefail; pytest tests/ ; git commit -m wip",
    ],
)
def test_mutation_sequenced_after_a_runner_nudges(_run, command):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    ctx = _nudge(res)
    assert ctx is not None, f"expected a sequencing nudge for {command!r}, got {res.stdout!r}"
    assert "state-mutating command follows" in ctx
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/ && git commit -m wip",
        "make done-gate && git push origin master",
        # No runner ran, so the guard has no verdict to protect.
        "ls -la ; git commit -m wip",
    ],
)
def test_gated_or_unjudged_mutation_gets_no_nudge(_run, command):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"unexpected nudge for {command!r}: {res.stdout!r}"


@pytest.mark.integration
def test_a_c_body_is_still_read_as_commands(_run):
    """A ``-c`` body is commands, so the guard must read it.

    Measured, not assumed: the guard was silent here BEFORE this card too — the
    old regex wanted the runner name space-bounded and a quote is not a space.
    So this pins new reach, and also pins that stripping quoted spans (which
    would otherwise delete this body along with the argument text) does not
    take it away again.
    """
    res = _run("bash -c 'pytest tests/ | tail'")
    ctx = _nudge(res)
    assert ctx is not None, f"the -c body went unread: {res.stdout!r}"
    assert "exit code masking detected" in ctx


@pytest.mark.integration
def test_the_nudge_survives_the_whole_bash_chain(shared_launcher, tmp_path_factory):
    """The guard is only useful if it is actually ON the chain and never gates.

    Runs the MATERIALIZED PreToolUse chain, not this script alone (HATS-1253):
    a single-hook test cannot see a peer overriding the reply.
    """
    import subprocess as sp

    from _helpers.hook_chain import (
        build_session_settings,
        pretooluse_hooks,
        run_chain,
        run_tool_chain,
    )

    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("hygiene-chain-home"))
    project = tmp_path_factory.mktemp("hygiene-chain-proj")
    res = sp.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert res.returncode == 0, f"self init failed:\n{res.stdout}\n{res.stderr}"
    settings = build_session_settings(project)

    # Positive control: without this the assertions below pass vacuously on a
    # chain that never loaded the guard at all.
    hooks = pretooluse_hooks(settings)
    assert any("tool_call_hygiene_guard.sh" in h for h in hooks), (
        f"the guard is not on the composed Bash chain: {hooks}"
    )

    verdict = run_chain(project, "pytest tests/ ; git commit -m wip", settings=settings, env=env)
    assert "state-mutating command follows" in verdict.context, verdict
    assert not verdict.gated, f"this guard must never gate, it only nudges: {verdict}"

    # The background branch reads a tool_input FIELD, not the command string, so
    # the flag has to survive the composed dispatcher — a single-hook test cannot
    # show that, and the fix would be dead in a real session with every unit test
    # green (HATS-1709). The pair IS the control: one payload, one field apart.
    # Bounded, because the command-lifetime guard REFUSES an unbounded background
    # launch — and its deny would be the chain's verdict, hiding whether this
    # guard said anything at all. The bound is what an agent has to write anyway.
    incident = "timeout 1800 bash scripts/gates.sh e2e > /tmp/e2e.log 2>&1; echo $? > /tmp/e2e.rc"
    foreground = run_tool_chain(project, "Bash", {"command": incident}, settings=settings, env=env)
    assert not foreground.context, f"the capture form preserves in the foreground: {foreground}"

    background = run_tool_chain(
        project,
        "Bash",
        {"command": incident, "run_in_background": True},
        settings=settings,
        env=env,
    )
    assert "completion notice" in background.context, background
    assert not background.gated, f"this guard must never gate, it only nudges: {background}"


# --- backgrounded runs: the notice reports the wrapper (HATS-1709) ------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        # The two measured incidents, verbatim in shape. Both captured the status
        # correctly AND were announced as "exit code 0", because the trailing
        # echo is what the completion notice reports.
        "bash scripts/ci-local.sh e2e > /tmp/e2e.log 2>&1; echo $? > /tmp/e2e.rc",
        "timeout 1800 bash -c 'bash scripts/gates.sh run unit' > /tmp/g.log 2>&1; echo \"GATES_EXIT=$?\" >> /tmp/g.log",
    ],
)
def test_a_backgrounded_capture_is_masking_not_preservation(_run, command):
    res = _run(command, background=True)
    assert res.returncode == 0, res.stderr
    ctx = _nudge(res)
    assert ctx is not None, f"expected a background nudge for {command!r}, got {res.stdout!r}"
    assert "completion notice" in ctx


@pytest.mark.integration
def test_the_same_capture_in_the_foreground_stays_excused(_run):
    """The excuse is not wrong, it is scoped: nothing consults a notice here."""
    command = "pytest tests/ > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc"
    assert _nudge(_run(command)) is None
    assert _nudge(_run(command, background=True)) is not None


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        # Redirect only: the wrapper's last element IS the runner, so the notice
        # tells the truth and there is nothing to say.
        "timeout 1800 bash scripts/gates.sh e2e > /tmp/e2e.log 2>&1",
        # The wrapper: capture inside, status out.
        "timeout 1800 bash runcheck.sh --log /tmp/e2e.log -- bash scripts/gates.sh e2e",
    ],
)
def test_a_background_run_whose_status_survives_gets_no_nudge(_run, command):
    res = _run(command, background=True)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"unexpected nudge for {command!r}: {res.stdout!r}"


# --- a MULTI-LINE quoted argument is still argument text (HATS-1709) ----------


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        # The role's commonest write, verbatim in shape: a work-log body that
        # talks about a run, and the `cd` that puts `; rack transition` on the
        # line. Both halves are needed — the text supplies the runner name, the
        # prefix supplies the mutation the guard then reports.
        "cd /repo; rack transition HATS-1 --log 'ran the tier\nbash scripts/gates.sh e2e came back red\nfixing now'",
        "cd /repo; rack transition HATS-1 --log 'gates.sh e2e is red\nsecond thought'",
        "cd /repo; rack create task --description 'we should run pytest here\nand ci-local.sh too'",
    ],
)
def test_a_multiline_quoted_argument_is_not_a_call(_run, command):
    """Quote stripping was line-oriented, so an opening quote whose pair sits on
    a later line never paired and the body was read as commands. Measured on the
    card's own work-log writes — the guard cried wolf on the text describing it."""
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"spurious nudge for {command!r}: {res.stdout!r}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        # The positive control the fix must not blind: a REAL runner on a later
        # line, outside any quote. Silence here would mean the contour went dark
        # rather than the noise going away.
        "cd /repo\nbash scripts/gates.sh run unit | tail",
        "echo starting\npytest tests/ ; git commit -m wip",
    ],
)
def test_a_real_runner_on_a_later_line_is_still_a_call(_run, command):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is not None, f"contour went dark for {command!r}"
