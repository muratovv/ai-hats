"""e2e (HATS-1647)

flow:   an agent edits a task card by hand instead of going through `rack`,
        while a teammate keeps authoring the same task's plan.md
cmds:
    rack transition HATS-1647 execute --log 'moved by the sanctioned writer'
    sed -i '' 's/^state:.*/state: done/' <ai_hats_dir>/tracker/backlog/tasks/HATS-1647/task.yaml  # no-resolve: the raw mutation the gate refuses
expect: writes under `<ai_hats_dir>/tracker/backlog/**` are denied and the
        refusal names `rack transition` plus its export-only kill switch, while
        `tasks/<ID>/plan.md` and files outside the tracker stay writable
why:    `rule_backlog_discipline` §1 has no automation behind it — a hand-edited
        task.yaml desynchronises the FSM, its locks and its audit trail, and the
        rule text alone has never stopped it

flow:   an agent working inside a linked worktree reaches back into the main
        checkout's tracker, its shell still carrying another checkout's AI_HATS_DIR
cmds:
    rack context HATS-1647
expect: the write is denied all the same, and by this gate — `ai_hats_dir` is
        resolved from the TARGET path's own ai-hats.yaml
why:    a worktree's project dir points at MAIN (HATS-524) and an inherited
        AI_HATS_DIR names a tracker of its own, so an env-based resolver would
        guard the wrong backlog while reporting success
"""

from __future__ import annotations

import json
import subprocess

import pytest

from _helpers.git import git, init_repo
from _helpers.hook_chain import (
    build_session_settings,
    pretooluse_hooks,
    run_chain,
    run_tool_chain,
)

pytestmark = pytest.mark.integration

TRACKER = ".agent/ai-hats/tracker/backlog"
KILL_SWITCH = "AI_HATS_BACKLOG_GATE_OFF"
DESTRUCTIVE_ACK = "AI_HATS_DESTRUCTIVE_ACK"


def assert_names_the_hatch(verdict):
    """HATS-1253 P4, in this gate's terms: the way through is `rack`, and the
    only override is exported by the supervisor for the whole session.

    ``Verdict.names_ack_flag`` cannot judge this one — its regex reads the
    letters `...B/ACK/LOG...` in the switch name as a consent flag."""
    assert "rack transition" in verdict.reason, f"deny must name the writer; got {verdict}"
    assert KILL_SWITCH in verdict.reason, f"deny must name the kill switch; got {verdict}"
    assert DESTRUCTIVE_ACK not in verdict.reason, (
        f"the backlog has no per-call consent flag; got {verdict}"
    )
    assert "=1 <command>" not in verdict.reason, (
        f"a per-call override is a guard the agent switches off; got {verdict}"
    )


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project whose composed session wires the Edit/Write hook chain."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("backlog-gate-home"))

    project = tmp_path_factory.mktemp("backlog-gate-proj")
    result = subprocess.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(f"self init failed:\n{result.stdout}\n{result.stderr}")
    (project / TRACKER / "tasks" / "HATS-1").mkdir(parents=True, exist_ok=True)
    (project / "src" / "app").mkdir(parents=True, exist_ok=True)
    # Two routes with the same shape and opposite answers: one lands in the
    # backlog, one does not. A resolver that follows links must tell them apart.
    (project / "blink").symlink_to(project / TRACKER)
    (project / "slink").symlink_to(project / "src")
    return project, env, build_session_settings(project)


def _write(project, env, settings, relpath: str):
    return run_tool_chain(
        project,
        "Write",
        {"file_path": str(project / relpath), "content": "state: done\n"},
        settings=settings,
        env=env,
    )


def test_writing_a_task_card_by_hand_is_denied(hooked_project):
    """The state file `rack` owns must not be reachable through Write.

    The refusal has to name the way through (HATS-1253 P4) — the sanctioned
    writer, and the export-only switch for the day the tracker itself is broken.
    A deny with nowhere to go sends the agent to blunt instruments instead."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, f"{TRACKER}/tasks/HATS-1/task.yaml")
    assert verdict.denied, f"a hand-written task.yaml must be denied; got {verdict}"
    assert_names_the_hatch(verdict)


def test_a_task_document_is_denied_with_the_attach_recipe(hooked_project):
    """`summary.md` and its kind are documents, not the plan — `rack` copies
    them in from outside. A deny that named only `--set` would strand the agent
    holding a finished summary with nowhere to put it."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, f"{TRACKER}/tasks/HATS-1/summary.md")
    assert verdict.denied, f"a document written in place must be denied; got {verdict}"
    assert "--attach" in verdict.reason, f"deny must name the way in; got {verdict}"


def test_plan_md_stays_writable(hooked_project):
    """The carve-out of `rule_backlog_discipline` §1b — the plan is authored by
    the agent, not minted by the FSM. Green before the gate exists and after."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, f"{TRACKER}/tasks/HATS-1/plan.md")
    assert not verdict.gated, f"plan.md is the sanctioned deliverable; got {verdict}"


def test_a_file_outside_the_tracker_is_untouched(hooked_project):
    """Same extension, different location — the gate keys on where, not what."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, "src/app/config.yaml")
    assert not verdict.gated, f"a file outside the tracker must pass; got {verdict}"


# --- The Bash side: closing the file tools alone just moves the hand ---------


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("echo 'state: done' > {card}", id="redirect"),
        pytest.param("echo 'state: done' >> {card}", id="redirect-append"),
        # `>|` is what the hand reaches for under `set -o noclobber`, and `&>`
        # is the everyday "and stderr too" — shlex hands both over as one token.
        pytest.param("echo 'state: done' >| {card}", id="redirect-clobber"),
        pytest.param("echo 'state: done' &> {card}", id="redirect-both-streams"),
        pytest.param("mkdir -p {tasks}/HATS-2", id="mkdir"),
        pytest.param("mv {card} {tasks}/HATS-2/task.yaml", id="mv"),
        pytest.param("cp /tmp/card.yaml {card}", id="cp"),  # noqa: S108 - payload string only
        pytest.param("rm -f {card}", id="rm"),
        pytest.param("ln -s /tmp/x {tasks}/HATS-2/task.yaml", id="ln-into-the-tracker"),  # noqa: S108 - payload string only
        pytest.param("sed -i '' 's/^state:.*/state: done/' {card}", id="sed-inplace"),
    ],
)
def test_raw_shell_mutation_of_the_tracker_is_denied(hooked_project, command):
    """Same rule, other hand. `sed -i` was already denied — but as ordinary
    in-place editing, whose refusal hands out a per-call ack. For the backlog
    there is no per-call form, so the tracker predicate has to answer first."""
    project, env, settings = hooked_project
    tasks = project / TRACKER / "tasks"
    verdict = run_chain(
        project,
        command.format(card=tasks / "HATS-1" / "task.yaml", tasks=tasks),
        settings=settings,
        env=env,
    )
    assert verdict.denied, f"{command!r} mutates the tracker and must be denied; got {verdict}"
    assert_names_the_hatch(verdict)


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("rack transition HATS-1 execute --log 'moved'", id="sanctioned-writer"),
        pytest.param("cat {card}", id="read-with-cat"),
        pytest.param("grep -n '^state:' {card}", id="read-with-grep"),
        pytest.param("cp {card} /tmp/card-backup.yaml", id="copy-out-is-a-read"),  # noqa: S108 - payload string only
        # Same asymmetry as `cp`: naming the card as the SOURCE writes nothing.
        pytest.param("ln {card} /tmp/card-hardlink", id="link-out-is-a-read"),  # noqa: S108 - payload string only
        pytest.param("mkdir -p {plan_dir}", id="mkdir-outside"),
    ],
)
def test_the_sanctioned_writer_and_plain_reads_pass(hooked_project, command):
    """Deliberately narrow: denying reads would cost more in false positives
    than a read of the backlog costs in tracker state (plan, Approach & counter)."""
    project, env, settings = hooked_project
    tasks = project / TRACKER / "tasks"
    verdict = run_chain(
        project,
        command.format(card=tasks / "HATS-1" / "task.yaml", plan_dir=project / "docs"),
        settings=settings,
        env=env,
    )
    assert not verdict.gated, f"{command!r} must pass the chain; got {verdict}"


# --- The hatch has to open the door it points at ---------------------------


def _switched_off(env):
    off = dict(env)
    off[KILL_SWITCH] = "1"
    return off


def test_the_exported_switch_opens_the_file_tools(hooked_project):
    """The half that already honoured it — kept as the positive control, so a
    switch that opens nothing is distinguishable from one that opens both."""
    project, env, settings = hooked_project
    verdict = run_tool_chain(
        project,
        "Write",
        {"file_path": str(project / TRACKER / "tasks" / "HATS-1" / "task.yaml")},
        settings=settings,
        env=_switched_off(env),
    )
    assert not verdict.gated, f"the exported switch must open Write; got {verdict}"


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("echo 'state: done' > {card}", id="redirect"),
        pytest.param("rm -f {card}", id="rm"),
        pytest.param("mv {card} {tasks}/HATS-2/task.yaml", id="mv"),
        pytest.param("mkdir -p {tasks}/HATS-2", id="mkdir"),
    ],
)
def test_the_exported_switch_opens_the_shell_too(hooked_project, command):
    """Emergency tracker repair IS raw shell. A switch the deny advertises but
    only the file tools honour points the agent at a wall."""
    project, env, settings = hooked_project
    tasks = project / TRACKER / "tasks"
    verdict = run_chain(
        project,
        command.format(card=tasks / "HATS-1" / "task.yaml", tasks=tasks),
        settings=settings,
        env=_switched_off(env),
    )
    assert not verdict.gated, f"{command!r} must open with the switch; got {verdict}"


def test_the_switch_does_not_disarm_the_generic_destructive_guard(hooked_project):
    """`sed -i` stays gated with the switch on — but by
    `global_rule_destructive_actions`, whose per-call ack is a real way out.
    Answering with the backlog text would re-advertise the switch that just
    failed to help: a hatch pointing at itself."""
    project, env, settings = hooked_project
    card = project / TRACKER / "tasks" / "HATS-1" / "task.yaml"
    verdict = run_chain(
        project,
        f"sed -i '' 's/^state:.*/state: done/' {card}",
        settings=settings,
        env=_switched_off(env),
    )
    assert verdict.gated, f"in-place editing is still destructive; got {verdict}"
    assert DESTRUCTIVE_ACK in verdict.reason, (
        f"with the backlog gate off, the generic guard must answer; got {verdict}"
    )
    assert KILL_SWITCH not in verdict.reason, (
        f"a deny must not re-offer the switch that is already on; got {verdict}"
    )


# --- A path is not a string: the two cheap dodges ---------------------------


def test_a_symlinked_route_into_the_backlog_is_denied(hooked_project):
    """`ln -s` costs one command, and a lexical matcher never sees it."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, "blink/tasks/HATS-1/task.yaml")
    assert verdict.denied, f"a symlinked route to the card must be denied; got {verdict}"
    assert_names_the_hatch(verdict)


def test_a_symlinked_route_gets_the_rack_recipe_in_the_shell_too(hooked_project):
    """Missing the route does worse than let `sed -i` past: the generic in-place
    deny answers instead, and its text hands out a per-call ack — a refusal that
    advertises the bypass this rule must not have."""
    project, env, settings = hooked_project
    card = project / "blink" / "tasks" / "HATS-1" / "task.yaml"
    verdict = run_chain(
        project, f"sed -i '' 's/^state:.*/state: done/' {card}", settings=settings, env=env
    )
    assert verdict.denied, f"a symlinked route must be denied in the shell; got {verdict}"
    assert_names_the_hatch(verdict)


def test_a_symlink_that_leads_elsewhere_still_passes(hooked_project):
    """The counter-test: following links must not turn every link into a deny."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, "slink/app/config.yaml")
    assert not verdict.gated, f"a link out of the tracker must pass; got {verdict}"


def test_a_case_folded_route_into_the_backlog_is_denied(hooked_project):
    """On a case-folding filesystem `.Agent/...` is the SAME file as `.agent/...`
    — confirmed by inode — so a case-sensitive match writes the card it refused."""
    project, env, settings = hooked_project
    folded = project / ".Agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / "HATS-1"
    if not folded.is_dir():
        pytest.skip("case-sensitive filesystem — the dodge does not exist here")

    verdict = _write(project, env, settings, str(folded.relative_to(project) / "task.yaml"))
    assert verdict.denied, f"a case-folded route must be denied; got {verdict}"
    assert_names_the_hatch(verdict)


# --- A hostile config must cost this gate only, and only quietly ------------


@pytest.fixture(scope="module")
def hostile_checkout(tmp_path_factory):
    """A checkout whose `ai-hats.yaml` the resolver cannot read to the end.

    `~nosuchuser` makes pathlib's `expanduser` raise RuntimeError — neither an
    OSError nor anything the resolver used to catch. Its tracker still sits at
    the documented default location, which is what the fallback is for."""
    root = tmp_path_factory.mktemp("hostile-checkout")
    (root / "ai-hats.yaml").write_text("ai_hats_dir: ~nosuchuser/tracker\n")
    (root / TRACKER / "tasks" / "HATS-9").mkdir(parents=True)
    (root / "src").mkdir()
    return root


def _run_hook(command: str, project, env, payload: str):
    """One hook of the composed chain, run as the harness runs it — so the exit
    code and stderr are visible. `run_chain` reads a crash as `allow`."""
    run_env = dict(env)
    run_env.setdefault("CLAUDE_PROJECT_DIR", str(project))
    return subprocess.run(  # noqa: S603 - command comes from our own settings.json
        ["bash", "-c", command],  # noqa: S607 - bash from PATH, as the harness runs it
        input=payload,
        cwd=str(project),
        env=run_env,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize("tool", ["Bash", "Write"])
def test_no_hook_dies_on_a_hostile_config(hooked_project, hostile_checkout, tool):
    """Requirement 4 is a QUIET allow, and quiet is an exit code.

    A hook that dies with a traceback still reads as `allow` to the harness, so
    the composite verdict cannot see the difference — only the process can."""
    project, env, settings = hooked_project
    victim = hostile_checkout / "src" / "app.py"
    tool_input = {"command": f"rm -f {victim}"} if tool == "Bash" else {"file_path": str(victim)}
    payload = json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    )

    for command in pretooluse_hooks(settings, tool):
        proc = _run_hook(command, project, env, payload)
        name = command.rsplit("/", 1)[-1]
        assert proc.returncode == 0, f"{name} exited {proc.returncode}:\n{proc.stderr}"
        assert "Traceback" not in proc.stderr, f"{name} crashed:\n{proc.stderr}"


def test_a_broken_config_falls_back_to_the_default_layout(hooked_project, hostile_checkout):
    """No config -> protected, broken config -> hole is the wrong way round.

    The failure happens AFTER the config is found, so a blanket catch upstream
    answers "not our business" for a card sitting in plain sight — inverting the
    intent the fallback was written for."""
    project, env, settings = hooked_project
    card = hostile_checkout / TRACKER / "tasks" / "HATS-9" / "task.yaml"

    verdict = _write(project, env, settings, str(card))

    assert verdict.denied, f"a card is a card even with the config broken; got {verdict}"
    assert_names_the_hatch(verdict)


def test_the_fallback_keeps_the_plan_carve_out(hooked_project, hostile_checkout):
    """Degrading to the default layout must not degrade into a blanket deny."""
    project, env, settings = hooked_project
    plan = hostile_checkout / TRACKER / "tasks" / "HATS-9" / "plan.md"

    verdict = _write(project, env, settings, str(plan))

    assert not verdict.gated, f"plan.md stays the agent's own file; got {verdict}"


def test_a_broken_config_does_not_deny_ordinary_files(hooked_project, hostile_checkout):
    """The counter-test: an unreadable config is not a reason to deny a checkout."""
    project, env, settings = hooked_project

    verdict = _write(project, env, settings, str(hostile_checkout / "src" / "app.py"))

    assert not verdict.gated, f"a file outside the tracker must pass; got {verdict}"


def test_a_hostile_config_does_not_take_the_rest_of_the_gate_with_it(
    hooked_project, hostile_checkout
):
    """The blast radius, measured. The tracker predicate runs on every path
    token of every mutating command, so an exception there stops `check_command`
    before `rm -rf /` is ever looked at — a regression far outside this card."""
    project, env, settings = hooked_project
    victim = hostile_checkout / "src" / "app.py"

    verdict = run_chain(project, f"rm -f {victim} && rm -rf /", settings=settings, env=env)

    assert verdict.denied, f"`rm -rf /` must still be denied alongside; got {verdict}"
    assert "filesystem root" in verdict.reason, (
        f"the catastrophic guard must be what answered; got {verdict}"
    )


# --- Which tracker? The one that owns the file (HATS-524) -------------------


@pytest.fixture(scope="module")
def linked_worktree(tmp_path_factory):
    """A second checkout carrying its own tracker, plus a real linked worktree.

    `.agent/` is gitignored as in a real project — which is why `wt_gate` waves
    a tracker write through and this gate is the only contour left on it."""
    main = tmp_path_factory.mktemp("other-checkout")
    (main / "ai-hats.yaml").write_text("ai_hats_dir: .agent/ai-hats\n")
    (main / ".gitignore").write_text(".agent/\n")
    (main / TRACKER / "tasks" / "HATS-9").mkdir(parents=True)
    (main / "src").mkdir()
    (main / "src" / "app.py").write_text("x = 1\n")
    init_repo(main, branch="master")
    git(main, "add", "-A")
    git(main, "commit", "-m", "seed")
    worktree = tmp_path_factory.mktemp("other-worktrees") / "wt"
    git(main, "worktree", "add", str(worktree), "-b", "task/x")
    return main, worktree


def test_a_worktree_session_still_cannot_touch_the_main_tracker(hooked_project, linked_worktree):
    """The session sits in the worktree and its AI_HATS_DIR names yet another
    checkout — the verdict follows the file, so it is denied anyway."""
    project, env, settings = hooked_project
    main, worktree = linked_worktree
    leaky = dict(env)
    leaky["AI_HATS_DIR"] = str(project / ".agent" / "ai-hats")
    leaky["CLAUDE_PROJECT_DIR"] = str(worktree)

    verdict = run_tool_chain(
        worktree,
        "Write",
        {"file_path": str(main / TRACKER / "tasks" / "HATS-9" / "task.yaml")},
        settings=settings,
        env=leaky,
    )
    assert verdict.denied, f"the main checkout's card must be denied; got {verdict}"
    assert verdict.hook == "backlog_write_gate.py", (
        f"a deny from another hook would prove nothing about this one; got {verdict}"
    )
    assert_names_the_hatch(verdict)


def test_ordinary_work_inside_the_worktree_is_untouched(hooked_project, linked_worktree):
    """The same session editing its own code — nothing to do with the tracker."""
    project, env, settings = hooked_project
    _main, worktree = linked_worktree
    leaky = dict(env)
    leaky["AI_HATS_DIR"] = str(project / ".agent" / "ai-hats")
    leaky["CLAUDE_PROJECT_DIR"] = str(worktree)

    verdict = run_tool_chain(
        worktree,
        "Write",
        {"file_path": str(worktree / "src" / "app.py")},
        settings=settings,
        env=leaky,
    )
    assert not verdict.gated, f"work inside the worktree must pass; got {verdict}"


# ----- a shell redirect token is not a path -----


def test_the_sanctioned_writer_survives_a_redirect_from_under_the_backlog(hooked_project):
    """The gate refused `rack … 2>&1`, and only from a cwd under the backlog.

    `shlex` splits `2>&1` into `['2','>&','1']`; the descriptor `1` was read as
    a relative path, so where the shell stood decided the verdict. From the
    repo root it passed, from a card directory it did not — which reads as
    flakiness rather than as a rule.
    """
    project, env, settings = hooked_project
    card_dir = project / TRACKER / "tasks" / "HATS-1"

    for tail in ("2>&1", "1>&2", "2>&-"):
        verdict = run_chain(
            project,
            f"rack transition HATS-1 --log probe {tail}",
            settings=settings,
            env=env,
            cwd=card_dir,
        )
        assert not verdict.gated, f"`rack … {tail}` must pass the chain; got {verdict}"

    control = run_chain(
        project, "rack transition HATS-1 --log probe", settings=settings, env=env, cwd=card_dir
    )
    assert not control.gated, f"control: the clean call must pass too; got {control}"


def test_a_copy_into_the_tracker_is_denied_with_a_redirect_appended(hooked_project):
    """The fail-open half. `cp`/`ln` are judged on their LAST path, and a
    trailing `2>&1` put the descriptor there — so the real destination was
    never examined and the copy landed in the tracker unchallenged."""
    project, env, settings = hooked_project
    card = project / TRACKER / "tasks" / "HATS-1" / "task.yaml"

    verdict = run_chain(project, f"cp /tmp/a {card} 2>&1", settings=settings, env=env)
    assert verdict.denied, f"the redirect must not hide the destination; got {verdict}"
    assert_names_the_hatch(verdict)

    control = run_chain(project, f"cp /tmp/a {card}", settings=settings, env=env)
    assert control.denied, f"control: the plain copy must be denied too; got {control}"


def test_a_real_file_redirect_into_the_tracker_is_still_denied(hooked_project):
    """`>&` takes a descriptor OR a filename. The fix discriminates on the
    operand, so this arm — the one that would be lost by dropping the operator
    from `REDIRECTS` — must stay."""
    project, env, settings = hooked_project
    card = project / TRACKER / "tasks" / "HATS-1" / "task.yaml"

    for command in (f"echo x > {card}", f"echo x >&{card}", f"echo x &> {card}"):
        verdict = run_chain(project, command, settings=settings, env=env)
        assert verdict.denied, f"{command!r} writes the card and must be denied; got {verdict}"
