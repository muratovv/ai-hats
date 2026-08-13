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

import subprocess

import pytest

from _helpers.git import git, init_repo
from _helpers.hook_chain import build_session_settings, run_chain, run_tool_chain

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
        pytest.param("mkdir -p {tasks}/HATS-2", id="mkdir"),
        pytest.param("mv {card} {tasks}/HATS-2/task.yaml", id="mv"),
        pytest.param("cp /tmp/card.yaml {card}", id="cp"),  # noqa: S108 - payload string only
        pytest.param("rm -f {card}", id="rm"),
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
