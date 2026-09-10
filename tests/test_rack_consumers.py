"""The check runner that makes ``checks:`` fire on rack FSM edges (HATS-1141).

Covers the seam pack itself (subscription surface, slot 15, in-lock phase) and
the per-binding outcome policy of ADR-0019 D4 — the part ``worktree_hooks``
deliberately does NOT share, since that channel is uniformly fail-closed.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import os
import stat
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from ai_hats_core import ComponentKind, CompositionResult, ResolvedCheck, ResolvedComponent
from ai_hats_rack.definition import BacklogDefinition, resolve_definition
from ai_hats_rack.dispatch import AbortOperation, DispatchContext, Phase
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.fsm import Topology
from ai_hats_rack.selectors import ANY, Selector
from ai_hats_rack.kernel import LOCK_TIMEOUT
from ai_hats_rack.models import TaskCard

from ai_hats import check_resolve
from ai_hats.check_points import resolve_checks
from ai_hats.check_resolve import CheckResolutionError
from ai_hats_core.deadline import Deadline

from ai_hats.hook_exec import run_hook
from ai_hats.models import AppBinding
from ai_hats_rack.checks import CheckSubscriber, check_subscriber

from ai_hats.rack_consumers import (
    CHECK_PRIORITY,
    EDGE_CHECK_TIMEOUT_S,
    AiHatsCheckPort,
    consumer_subscribers,
)


def _extension(project_dir, *, tasks_dir, topology, resolve=None, **kwargs) -> CheckSubscriber:
    """The channel as production wires it: the rack\'s subscriber over ai-hats\'s
    port (ADR-0019 D11). ``resolve`` injects the carried rows, the seam the old
    ``CheckRunnerExtension`` exposed for the same reason."""
    kwargs.setdefault("backlog", "tasks")
    return CheckSubscriber(
        AiHatsCheckPort(
            None if project_dir is None else ProjectLayout.at(project_dir),
            catalog=tasks_dir,
            resolve=resolve,
        ),
        topology=topology,
        **kwargs,
    )


def _definition(topology: Topology, *, tmp_path: Path) -> BacklogDefinition:
    """The packaged definition with this test's topology grafted on — the pack
    takes a definition since HATS-1575 (the rack derives selectors from it)."""
    packaged = resolve_definition(tmp_path / "unwritten", project_dir=tmp_path)
    return replace(packaged, name="tasks", cli_alias=None, topology=topology)


def _topology() -> Topology:
    return Topology(
        initial="open",
        states=("open", "review", "done"),
        edges={"open": ("review",), "review": ("done",), "done": ()},
    )


def _script(tmp_path: Path, body: str, *, name: str = "gate.sh", executable: bool = True) -> Path:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _check(script: Path, *, point: str = "review->done", on_error: str = "refuse"):
    return ResolvedCheck(
        app="rack",
        path=("tasks",),
        run=f"quality::gates/{script.name}",
        at=(point,),
        cargo={},
        on_error=on_error,
        script_path=script,
        declared_by="maintainer",
    )


def _ctx(
    event_key: str = "review->done",
    *,
    task_id: str = "T-1",
    lock_expires_at: float | None = None,
) -> DispatchContext:
    src, dst = event_key.split("->")
    return DispatchContext(
        event=EdgeEvent(from_state=src, to_state=dst),
        task=TaskCard(id=task_id),
        caller_cwd=Path.cwd(),
        is_epic=False,
        actor="test",
        lock_expires_at=lock_expires_at,
    )


def _runner(tmp_path: Path, *checks: ResolvedCheck, **kwargs) -> CheckSubscriber:
    return _extension(
        tmp_path,
        tasks_dir=tmp_path / "tasks",
        topology=_topology(),
        resolve=lambda: tuple(checks),
        **kwargs,
    )


def test_pack_covers_every_edge_of_the_given_topology(tmp_path):
    """R2 + R7: the pack is no longer empty, it covers every move of the topology
    handed through the seam (never a re-opened one), and it books slot 15 in-lock.

    Said as one wide selector since HATS-1720 — so the topology is what the
    subscription is checked AGAINST rather than what it spells. The typed-object
    assertion stays load-bearing either way: a plain arrow STRING would land in
    the non-FSM bucket and match nothing.
    """
    topology = _topology()
    pack = consumer_subscribers(
        ProjectLayout.at(tmp_path),
        definition=_definition(topology, tmp_path=tmp_path),
        catalog=tmp_path / "tasks",
    )

    assert pack, "the consumer pack must carry the check runner"
    subs = [spec for sub in pack for spec in sub.subscriptions()]
    assert {spec.selector for spec in subs} == {Selector(ANY, ANY)}
    assert {spec.phase for spec in subs} == {Phase.IN_LOCK}
    assert {spec.priority for spec in subs} == {15}
    assert CHECK_PRIORITY == 15


def test_the_pack_judges_rows_against_the_topology_handed_through_the_seam(tmp_path):
    """R7, and it needs its own test now that the subscription is topology-free.

    While the pack ENUMERATED the product, re-opening the packaged `backlog.yaml`
    instead of taking `definition.topology` changed the subscription set and the
    assertion above caught it. One wide selector is the same whatever topology it
    came from, so the seam has to be pinned where it is still observable: the
    subscriber judges a row against the topology it holds, and `open->review`
    exists only in the one handed in — the packaged file has no `open` state at all.
    """
    packaged = resolve_definition(tmp_path / "unwritten", project_dir=tmp_path).topology
    assert "open" not in packaged.states, (
        "the packaged topology must NOT hold this state, or the test proves nothing"
    )
    script = _script(tmp_path, "echo 'the row fired'\nexit 1")
    # Through `check_subscriber`, because THAT is the seam: it is the one line
    # that decides which topology the subscriber judges rows against.
    runner = check_subscriber(
        _definition(_topology(), tmp_path=tmp_path),
        port=AiHatsCheckPort(
            ProjectLayout.at(tmp_path),
            catalog=tmp_path / "tasks",
            resolve=lambda: (_check(script, point="open->review"),),
        ),
    )

    # A REFUSING script, because a passing one proves nothing: a row skipped for a
    # topology that never had `open->review` also returns None.
    with pytest.raises(AbortOperation):
        runner.on_event(_ctx("open->review"))


def test_an_unowned_backlog_composes_nothing_and_says_so(tmp_path, capsys):
    """A backlog nobody owns declares nothing, so nothing fires — said out loud,
    or a gate that is not there reads as a gate that passed."""
    tasks_dir = tmp_path / "scratch" / "tasks"
    port = AiHatsCheckPort(None, catalog=tasks_dir)

    assert port.check_declarations() == ()

    said = capsys.readouterr().err
    assert "no project owns" in said
    assert str(tasks_dir) in said


def test_a_bound_check_on_an_unowned_backlog_refuses_in_words(tmp_path):
    """The guard on the road an injected resolver can still reach: no owner means
    no project declared these rows, and this channel says so in its own typed
    refusal — a traceback out of an in-lock subscriber is a defect, not a message.
    """
    script = _script(tmp_path, "exit 0")
    runner = _extension(
        None,
        tasks_dir=tmp_path / "tasks",
        topology=_topology(),
        resolve=lambda: (_check(script),),
    )

    with pytest.raises(CheckResolutionError) as exc_info:
        runner.on_event(_ctx())

    assert str(tmp_path / "tasks") in str(exc_info.value)
    assert "no project owns" in str(exc_info.value) or "which no project owns" in str(
        exc_info.value
    )


def _wt_state(project_dir: Path, task_id: str, worktree: Path) -> Path:
    """The worktree-state record rack writes at execute, as the runner reads it."""

    state_dir = ProjectLayout.at(project_dir).sessions.worktrees
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"task-{task_id.lower()}.json"
    state_path.write_text(
        json.dumps(
            {
                "branch": f"task/{task_id.lower()}",
                "worktree_path": str(worktree),
                "original_branch": "master",
            },
            indent=2,
        )
    )
    return state_path


def test_the_runner_hands_every_check_the_tasks_dir_of_this_transition(tmp_path):
    """R4 / HATS-1540: role scope is not backlog scope.

    One role fires on EVERY backlog the rack CLI touches, scratch ``--tasks-dir``
    included — that is what turned master red in HATS-1538. Without the dir a
    script cannot tell "this card is not mine" from "``AI_HATS_DIR`` leaked", so
    the runner states which backlog the transition runs against. Asserted by
    dumping the child's environment, never by reading the source.
    """
    scratch = tmp_path / "elsewhere" / "tasks"
    scratch.mkdir(parents=True)
    script = _script(tmp_path, 'echo "${AI_HATS_TASKS_DIR-unset}"\nexit 2')
    runner = _extension(
        tmp_path, tasks_dir=scratch, topology=_topology(), resolve=lambda: (_check(script),)
    )

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert exc_info.value.reason == str(scratch)


def test_the_runner_resolves_the_worktree_so_no_gate_parses_the_state_json(tmp_path):
    """R2 / HATS-1540: one resolution, not one per gate.

    Every FSM gate used to re-derive
    ``<ai_hats_dir>/sessions/worktrees/task-<id>.json`` and ``jq`` the path out.
    The primitive has taken ``worktree_path`` since HATS-1151 and no edge point
    computed it — so the same lookup was open-coded in shell, three spellings of
    it, each able to judge the wrong tree on its own.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _wt_state(tmp_path, "T-1", worktree)
    script = _script(tmp_path, 'echo "${AI_HATS_WORKTREE_PATH-unset}"\nexit 2')
    runner = _runner(tmp_path, _check(script))

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx(task_id="T-1"))

    assert exc_info.value.reason == str(worktree)


def test_an_ambient_tasks_dir_never_reaches_a_check(tmp_path, monkeypatch):
    """HATS-1540 review: the primitive OWNS this variable, so a point that does
    not resolve a backlog removes it instead of inheriting it.

    Left to ``extra_env`` — which can only add — a stale ``AI_HATS_TASKS_DIR``
    from the ambient environment reached the gate at ``wt:pre-merge``, the script
    compared it to its own tracker, read "not my backlog" and waved an unmarked
    branch into master. Measured on the real script, not feared.
    """
    monkeypatch.setenv("AI_HATS_TASKS_DIR", "/somewhere/else/tasks")
    script = _script(tmp_path, 'echo "${AI_HATS_TASKS_DIR-unset}"\nexit 2')

    run = run_hook(
        script,
        point="wt:pre-merge",
        budget=10,
        deadline=Deadline.without_lock(10, why="unit test"),
        project_dir=tmp_path,
    )

    assert run.reason == "unset"


def test_the_edge_runner_overwrites_an_ambient_tasks_dir(tmp_path, monkeypatch):
    """The other half: where a backlog IS resolved, its value wins over the
    ambient one rather than being merged with it."""
    monkeypatch.setenv("AI_HATS_TASKS_DIR", "/somewhere/else/tasks")
    scratch = tmp_path / "real" / "tasks"
    scratch.mkdir(parents=True)
    script = _script(tmp_path, 'echo "${AI_HATS_TASKS_DIR-unset}"\nexit 2')
    runner = _extension(
        tmp_path, tasks_dir=scratch, topology=_topology(), resolve=lambda: (_check(script),)
    )

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert exc_info.value.reason == str(scratch)


def test_a_card_with_no_worktree_gets_no_stale_path(tmp_path, monkeypatch):
    """ADR-0019 D5/D7: a wrong-tree pass is worse than an absent variable.

    The ambient environment of whoever launched the session may carry another
    worktree's path — an epic, a forced execute and a card whose tree was
    discarded all reach a gate with no tree of their own.
    """
    monkeypatch.setenv("AI_HATS_WORKTREE_PATH", "/stale/from/another/worktree")
    script = _script(tmp_path, 'echo "${AI_HATS_WORKTREE_PATH-unset}"\nexit 2')
    runner = _runner(tmp_path, _check(script))

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx(task_id="T-1"))

    assert exc_info.value.reason == "unset"


def test_a_record_whose_worktree_is_gone_reads_as_no_worktree(tmp_path, monkeypatch):
    """A swept ``$TMPDIR`` leaves the record behind; the tree is what matters.

    And the read is PURE: ``load_for_task`` unlinks such a record, which would
    make a refused transition mutate lifecycle state on its way out.
    """
    monkeypatch.setenv("AI_HATS_WORKTREE_PATH", "/stale/from/another/worktree")
    state_path = _wt_state(tmp_path, "T-1", tmp_path / "swept-away")
    script = _script(tmp_path, 'echo "${AI_HATS_WORKTREE_PATH-unset}"\nexit 2')
    runner = _runner(tmp_path, _check(script))

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx(task_id="T-1"))

    assert exc_info.value.reason == "unset"
    assert state_path.is_file(), "a refused transition must not delete worktree state"


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda p: p.write_text("{not json"), id="malformed-json"),
        pytest.param(lambda p: p.write_text(""), id="empty-file"),
        pytest.param(lambda p: (p.unlink(), p.mkdir()), id="directory-not-file"),
    ],
)
def test_a_state_file_that_cannot_be_READ_refuses_rather_than_gating_no_tree(tmp_path, corrupt):
    """ "Cannot tell" is not "no worktree" — and nothing asserted it until now.

    A gate handed no ``AI_HATS_WORKTREE_PATH`` reads it as "this card brings no
    commits, nothing to gate" and passes (``done-gate.sh`` F-11). So an
    unreadable record must abort, not answer None. The behaviour shipped with
    HATS-1540 and its review found that mutating the refusal back to
    ``return None`` broke ZERO of 3358 tests — this is that hole closed.
    """
    state_path = _wt_state(tmp_path, "T-1", tmp_path / "wt")
    corrupt(state_path)
    runner = _runner(tmp_path, _check(_script(tmp_path, "exit 0")))

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx(task_id="T-1"))

    assert "could not be resolved" in exc_info.value.reason
    assert "T-1" in exc_info.value.reason


def test_one_worktree_lookup_serves_every_binding_on_the_edge(tmp_path):
    """Two bindings on one edge resolve the tree once — it cannot change between
    them, and the lookup takes a lock. Since HATS-1541 the rack calls the port
    once per row, so the "once" is the memo in the port, not the call count."""
    calls: list[str] = []
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _wt_state(tmp_path, "T-1", worktree)
    runner = _runner(
        tmp_path,
        _check(_script(tmp_path, "exit 0", name="a.sh")),
        _check(_script(tmp_path, "exit 0", name="b.sh")),
    )
    port = runner._port
    original = port._lookup_worktree

    def counting(task_id):
        calls.append(task_id)
        return original(task_id)

    port._lookup_worktree = counting
    runner.on_event(_ctx(task_id="T-1"))

    assert calls == ["T-1"]


def test_no_binding_on_the_edge_resolves_no_worktree(tmp_path):
    """The lookup takes a filelock, so an edge with nothing bound must not pay
    for it — the overwhelmingly common case on every transition."""
    runner = _runner(tmp_path)  # nothing bound
    called = []
    runner._port._lookup_worktree = lambda task_id: called.append(task_id)

    assert runner.on_event(_ctx()) is None
    assert called == []


def test_refuse_aborts_the_transition_with_the_verbatim_reason(tmp_path):
    """R3.3: exit 2 is a verdict, and the reason is the child's stdout tail —
    undiluted, so the agent reads the check's own words in ``--json``."""
    script = _script(tmp_path, "echo 'drain the review notes first'\nexit 2")
    runner = _runner(tmp_path, _check(script))

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert exc_info.value.reason == "drain the review notes first"


def test_corrupt_is_not_softened_by_on_error_warn(tmp_path):
    """R4: ``warn`` governs the check's own exit status. A script that is not
    there is corruption — softening it would make deleting a file a way to
    disarm a gate (ADR-0019 D4)."""
    missing = tmp_path / "vanished.sh"
    runner = _runner(tmp_path, _check(missing, on_error="warn"))

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    reason = exc_info.value.reason
    assert "the check did not run" in reason  # class (b): no bytes, so no verdict
    assert "quality::gates/vanished.sh" in reason
    assert "hook" not in reason, "a binding line is not a hook channel (HATS-1572)"


def test_broke_under_warn_proceeds_and_leaves_a_work_log_trace(tmp_path):
    """R4: ``BROKE`` is the one downgradable class — the transition proceeds,
    but never in silence."""
    script = _script(tmp_path, "echo 'ruff exploded'\nexit 1")
    runner = _runner(tmp_path, _check(script, on_error="warn"))

    delta = runner.on_event(_ctx())

    assert delta is not None
    trace = "\n".join(delta.work_log)
    assert "downgraded by on_error: warn" in trace
    assert "quality::gates/gate.sh" in trace
    assert "ruff exploded" in trace


def test_pass_leaves_no_delta_and_writes_the_log_beside_the_card(tmp_path):
    """R3.4 + F-4: the log lands under the card's dot-directory, with ``:``
    sanitised out of the event key so the name is a legal filename."""
    script = _script(tmp_path, "echo 'all good'")
    runner = _runner(tmp_path, _check(script))

    assert runner.on_event(_ctx()) is None

    assert "all good" in _by_stem(
        _logs(tmp_path), "review-%3Edone~rack~tasks~quality+gates~gate.sh"
    )


def test_a_resolution_failure_is_a_typed_refusal_not_a_traceback(tmp_path):
    """R8: the in-lock dispatcher re-raises anything that is not an
    ``AbortOperation`` verbatim, so an unconverted error reaches the user as a
    stack instead of a reason."""

    def boom() -> tuple[ResolvedCheck, ...]:
        raise CheckResolutionError("active role 'ghost' does not exist")

    runner = _extension(tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology(), resolve=boom)

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert "active role 'ghost' does not exist" in exc_info.value.reason


def test_an_unparseable_project_config_refuses_instead_of_tracebacking(tmp_path):
    """R8: the declaration probe reads ``ai-hats.yaml``, so its parse errors are
    this channel's errors. A project declaring NOTHING still reached
    ``ProjectConfig.from_yaml`` before the fail-closed boundary, and
    ``ProjectConfigError`` is neither ``OSError`` nor one of the typed two — so
    every transition ended in a stack trace."""
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 99\n")
    runner = _extension(tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology())

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert "ai-hats.yaml" in exc_info.value.reason
    assert "schema_version 99" in exc_info.value.reason


def test_a_project_with_no_active_role_takes_its_edges_untouched(tmp_path):
    """A builtin binding must not brick every role-less project (HATS-1137).

    ``declares_checks`` answers "is a declaration in reach", and the builtin
    library is in reach of everyone — so the moment a builtin trait or role
    ships one row, the probe says True for projects that never opted in. With
    no active role there is no composition and therefore no binding (D7), so
    the honest answer is "nothing bound", not a refusal.
    """
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 1\n")
    runner = _extension(tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology())

    assert runner.on_event(_ctx()) is None


def test_a_role_that_is_set_but_unresolvable_still_refuses(tmp_path):
    """The other side of the line above: absence of a role is not a defect,
    a named role that does not compose is.

    The declaration is project-local on purpose. Reading it out of the ambient
    library made this test pass only while some shipped role happened to carry a
    ``checks:`` row — HATS-1538 withdrew that row and the test went red without
    the behaviour changing at all.
    """
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 1\nactive_role: ghost\ndefault_role: ghost\n"
    )
    declaring = tmp_path / "libraries" / "roles" / "declares"
    declaring.mkdir(parents=True)
    (declaring / "config.yaml").write_text(
        "name: declares\ncomposition:\n  checks:\n"
        "    - {skill: s, script: hooks/x.sh, on: ['review->done']}\n"
    )
    assert check_resolve.declares_checks(tmp_path) is True, "precondition: probe must see it"
    runner = _extension(tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology())

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert "ghost" in exc_info.value.reason


# ---------------------------------------------------------------------------
# several bindings on one point (HATS-1137)
# ---------------------------------------------------------------------------


def _stems(logs: dict[str, str]) -> list[str]:
    """Log names without the identity digest ``check_log_token`` appends.

    The digest is what keeps two rows off one file (HATS-1137); these assertions
    are about WHICH binding wrote WHICH log, so they pin the readable stem and
    let the discriminator vary.
    """
    return sorted(name.rsplit("~", 1)[0] for name in logs)


def _by_stem(logs: dict[str, str], stem: str) -> str:
    matches = [body for name, body in logs.items() if name.rsplit("~", 1)[0] == stem]
    assert len(matches) == 1, f"expected exactly one log for {stem!r}, got {sorted(logs)}"
    return matches[0]


def _logs(tmp_path: Path, task_id: str = "T-1") -> dict[str, str]:
    """Every check log left beside one card, by filename."""
    return {
        p.name: p.read_text() for p in sorted((tmp_path / "tasks" / task_id / ".checks").iterdir())
    }


def test_two_bindings_on_one_edge_each_keep_their_own_log(tmp_path):
    """The log name carries a binding discriminator, so binding #2 cannot wipe
    #1's file. ``run_hook`` opens the log ``"wb"``, so one name per edge left
    only the last run's bytes on disk — while ``_note_truncation`` kept pointing
    #1's reason at that path, which is a wrong pointer, not a missing one."""
    first = _script(tmp_path, "echo 'first ran'", name="first.sh")
    second = _script(tmp_path, "echo 'second ran'", name="second.sh")
    runner = _runner(tmp_path, _check(first), _check(second))

    assert runner.on_event(_ctx()) is None

    logs = _logs(tmp_path)
    assert _stems(logs) == [
        "review-%3Edone~rack~tasks~quality+gates~first.sh",
        "review-%3Edone~rack~tasks~quality+gates~second.sh",
    ]
    assert "first ran" in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~first.sh")
    assert "second ran" not in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~first.sh")
    assert "second ran" in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~second.sh")


def test_the_log_name_carries_the_namespaced_skill_and_the_script_path(tmp_path):
    """The discriminator is the dedup identity — namespaced skill plus script —
    and both can carry separators (``dev::python``, ``hooks/done-gate.sh``), so
    both are escaped into one filename component that stays readable."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    check = ResolvedCheck(
        app="rack",
        path=("tasks",),
        run="dev::python/hooks/done-gate.sh",
        at=("review->done",),
        cargo={},
        on_error="refuse",
        script_path=_script(hooks, "echo 'nested ran'", name="done-gate.sh"),
        declared_by="maintainer",
    )

    assert _runner(tmp_path, check).on_event(_ctx()) is None

    logs = _logs(tmp_path)
    assert _stems(logs) == ["review-%3Edone~rack~tasks~dev+python~hooks+done-gate.sh"]
    assert "nested ran" in _by_stem(logs, "review-%3Edone~rack~tasks~dev+python~hooks+done-gate.sh")


def test_two_bindings_that_flatten_alike_still_get_two_logs(tmp_path):
    """Escaping, not replacing. A naive ``/`` → ``-`` would give ``a/b.sh`` and
    ``a-b.sh`` one filename, which is the very defect again — so the separator
    a script path collapses to is one no unescaped component can contain."""
    nested = tmp_path / "a"
    nested.mkdir()
    dashed = _check(_script(tmp_path, "echo 'dashed ran'", name="a-b.sh"))
    slashed = replace(
        _check(_script(nested, "echo 'slashed ran'", name="b.sh")),
        run="quality::gates/a/b.sh",
    )

    assert _runner(tmp_path, slashed, dashed).on_event(_ctx()) is None

    logs = _logs(tmp_path)
    assert _stems(logs) == [
        "review-%3Edone~rack~tasks~quality+gates~a+b.sh",
        "review-%3Edone~rack~tasks~quality+gates~a-b.sh",
    ]
    assert "slashed ran" in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~a+b.sh")
    assert "dashed ran" in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~a-b.sh")


def test_retrying_the_edge_overwrites_that_bindings_own_log(tmp_path):
    """One file per (task, edge, binding): a retry replaces its own previous
    transcript rather than growing a pile beside the card."""
    runner = _runner(tmp_path, _check(_script(tmp_path, "echo 'first attempt'")))
    assert runner.on_event(_ctx()) is None

    _script(tmp_path, "echo 'second attempt'")  # same binding, new bytes
    assert runner.on_event(_ctx()) is None

    logs = _logs(tmp_path)
    assert _stems(logs) == ["review-%3Edone~rack~tasks~quality+gates~gate.sh"]
    assert "second attempt" in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~gate.sh")
    assert "first attempt" not in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~gate.sh")


def test_bindings_run_in_composition_order_and_the_runner_never_re_sorts(tmp_path):
    """Composition order is traits in declaration order, then the role's own
    rows (``composer._resolve_traits`` before the role's own ``extend``, pinned
    by ``test_compose_collects_checks_traits_before_role``). ``_bound_to``
    filters that tuple by point without reordering it, so the names below are
    deliberately anti-alphabetical and one foreign point sits in the middle."""
    order = tmp_path / "order.txt"
    scripts = [
        _script(tmp_path, f"echo {name} >> '{order}'", name=f"{name}.sh")
        for name in ("c", "a", "b")
    ]
    elsewhere = _check(_script(tmp_path, f"echo z >> '{order}'", name="z.sh"), point="open->review")
    runner = _runner(
        tmp_path, _check(scripts[0]), elsewhere, _check(scripts[1]), _check(scripts[2])
    )

    assert runner.on_event(_ctx()) is None

    assert order.read_text().split() == ["c", "a", "b"]


def test_the_first_refusal_stops_every_later_binding(tmp_path):
    """Fail on first: the loop raises, so #2 and #3 never spawn at all."""
    refusing = _script(tmp_path, "echo 'drain the review notes first'\nexit 2", name="refuse.sh")
    later = [_script(tmp_path, f"touch ran-{n}", name=f"{n}.sh") for n in ("second", "third")]
    runner = _runner(tmp_path, _check(refusing), *(_check(s) for s in later))

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert exc_info.value.reason == "drain the review notes first"
    assert not (tmp_path / "ran-second").exists()
    assert not (tmp_path / "ran-third").exists()
    assert _stems(_logs(tmp_path)) == ["review-%3Edone~rack~tasks~quality+gates~refuse.sh"]


def test_a_break_downgraded_by_warn_lets_the_next_binding_run(tmp_path):
    """The other side of fail-on-first: ``on_error: warn`` on a BROKE outcome
    continues the loop, so the bindings behind it still run and still log."""
    broke = _script(tmp_path, "echo 'ruff exploded'\nexit 1", name="broke.sh")
    after = _script(tmp_path, "touch ran-second\necho 'second ran'", name="second.sh")
    runner = _runner(tmp_path, _check(broke, on_error="warn"), _check(after))

    delta = runner.on_event(_ctx())

    assert delta is not None
    assert "downgraded by on_error: warn" in "\n".join(delta.work_log)
    assert (tmp_path / "ran-second").is_file()
    logs = _logs(tmp_path)
    assert "ruff exploded" in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~broke.sh")
    assert "second ran" in _by_stem(logs, "review-%3Edone~rack~tasks~quality+gates~second.sh")


def test_a_deduped_binding_keeps_its_first_slot_and_the_strictest_policy(tmp_path):
    """``check_points._stricter`` collapses a repeated (skill, script, point)
    into the FIRST declaration's slot while hardening ``on_error`` to the
    strictest. Both halves have a runtime consequence, and this pins them there:
    the re-declared row runs FIRST because it kept slot 0, and its exit 1 aborts
    because the later ``refuse`` won — so the row declared between them, which
    a last-slot dedup would have run first, never runs at all."""
    skill_dir = tmp_path / "skills" / "gate-skill"
    skill_dir.mkdir(parents=True)
    _script(skill_dir, "echo 'ruff exploded'\nexit 1", name="a.sh")
    _script(skill_dir, "touch ran-b", name="b.sh")
    composed = ResolvedComponent(
        name="gate-skill", component_type=ComponentKind.SKILL, source_path=skill_dir
    )
    point = "review->done"  # an edge of _topology(): the rack subscribes by its own

    def row(script: str, on_error: str, declared_by: str) -> AppBinding:
        return AppBinding(
            declared_by=declared_by,
            app="rack",
            path=("tasks",),
            run=f"gate-skill/{script}",
            at=(point,),
            on_error=on_error,
            cargo={},
        )

    resolved = resolve_checks(
        [
            row("a.sh", "warn", "trait-x"),
            row("b.sh", "refuse", "trait-y"),
            row("a.sh", "refuse", "role-z"),
        ],
        [composed],
    )

    assert [(c.declared_by, c.script, c.on_error) for c in resolved] == [
        ("trait-x", "a.sh", "refuse"),
        ("trait-y", "b.sh", "refuse"),
    ]

    with pytest.raises(AbortOperation) as exc_info:
        _runner(tmp_path, *resolved).on_event(_ctx(point))

    assert "ruff exploded" in exc_info.value.reason
    assert not (tmp_path / "ran-b").exists(), "the hardened first slot must stop the rest"


# ---------------------------------------------------------------------------
# resolution (ADR-0019 D9) — two modes, one composition
# ---------------------------------------------------------------------------


def _library(root: Path, *, declares: bool) -> Path:
    trait = root / "traits" / "maintainer"
    trait.mkdir(parents=True)
    body = "name: maintainer\ncomposition:\n  skills:\n    - quality::gates\n"
    if declares:
        body += (
            "  apps:\n"
            "    rack:\n"
            "      tasks:\n"
            "        - run: quality::gates/gate.sh\n"
            "          at:\n"
            "            - review->done\n"
        )
    (trait / "config.yaml").write_text(body)
    return root


#: The skill every ``_check`` above declares. Composed, because a binding never
#: pulls its skill in (ADR-0019 D2) — and since HATS-1540 the mirror's leaf name
#: comes from the COMPOSED skill, so a composition without it resolves to nothing.
_GATE_SKILL = ResolvedComponent(
    name="quality::gates",
    component_type=ComponentKind.SKILL,
    source_path=Path("/nonexistent/quality-gates"),
)


def _composition(*, checks: tuple[ResolvedCheck, ...] = (), errors: list[str] | None = None):
    return CompositionResult(
        name="maintainer",
        priorities=[],
        rules=[],
        skills=[_GATE_SKILL],
        injections=[],
        errors=errors or [],
        checks=checks,
    )


def _mirror_root(project_dir: Path, session_id: str) -> Path:
    """Where the stub surface below mirrors this session's composed skills."""

    return ProjectLayout.at(project_dir).cache.session(session_id) / "mirror"


def _identity(project_dir: Path, session_id: str, skills_root: Path | None = None):
    """The envelope a session carries — where the root now comes from (HATS-1594).

    Through HATS-1593 an autouse fixture stubbed ``providers.get_surface`` here,
    because the channel asked the registry for the root on every resolve. It no
    longer does: the surface answers once at launch and the answer rides the
    envelope, so the root is stated by the test instead of stubbed behind it.
    """
    from ai_hats.session_identity import SessionIdentity

    root = _mirror_root(project_dir, session_id) if skills_root is None else skills_root
    return SessionIdentity(
        id=session_id,
        role="gate-role",
        provider="stub",
        project_dir=project_dir,
        session_dir=project_dir / ".agent" / "runs" / f"session_{session_id}",
        skills_root=str(root),
    )


def test_a_project_without_declarations_never_composes(tmp_path, monkeypatch):
    """S3: composition is ~10x the cost of the declaration probe, and a project
    with no ``checks:`` must not pay it on every transition."""
    monkeypatch.setattr(
        check_resolve, "_library_roots", lambda _p: [_library(tmp_path / "lib", declares=False)]
    )
    monkeypatch.setattr(
        check_resolve,
        "_compose_role",
        lambda _p, _identity=None: pytest.fail("composed a project that declares no checks"),
    )
    # HATS-1594: and it must not be REFUSED either. Reading the envelope before
    # the probe made a half-identified session abort every transition of a
    # project that never asked for a gate.
    monkeypatch.setenv("AI_HATS_SESSION_ID", "an-older-builds-session")
    monkeypatch.delenv("AI_HATS_SESSION_IDENTITY", raising=False)

    assert check_resolve.resolve_carried_checks(tmp_path, "rack") == ()


def test_a_declared_binding_refuses_a_half_identified_session(tmp_path, monkeypatch):
    """The other side of it: once a binding IS in reach it has to be rooted, and
    rooting it against a session nobody can name is the silence this channel
    exists to remove. Nothing is patched on purpose — this repository's own
    builtin library declares bindings, so the probe answers True for real."""
    monkeypatch.setenv("AI_HATS_SESSION_ID", "an-older-builds-session")
    monkeypatch.delenv("AI_HATS_SESSION_IDENTITY", raising=False)

    with pytest.raises(CheckResolutionError, match="too old to say what it is"):
        check_resolve.resolve_carried_checks(tmp_path, "rack")


def test_a_declared_binding_is_detected_by_the_byte_probe(tmp_path, monkeypatch):
    """The other half of S3: the cheap probe must not miss a real declaration,
    or the early exit becomes a silent disarm."""
    monkeypatch.setattr(
        check_resolve, "_library_roots", lambda _p: [_library(tmp_path / "lib", declares=True)]
    )

    assert check_resolve.declares_checks(tmp_path) is True


def test_a_broken_composition_refuses_instead_of_passing_quietly(tmp_path, monkeypatch):
    """R6: ``compose_for_carry`` swallows every exception and returns ``None``.
    That is a deliberate fail-open for worktree carry and the HYP-078 hole here."""
    monkeypatch.setattr(
        check_resolve, "_library_roots", lambda _p: [_library(tmp_path / "lib", declares=True)]
    )
    monkeypatch.setattr(
        check_resolve,
        "_compose_role",
        lambda _p, _identity=None: (_ for _ in ()).throw(RuntimeError("library schema is newer")),
    )

    with pytest.raises(CheckResolutionError) as exc_info:
        check_resolve.resolve_carried_checks(tmp_path, "rack")

    assert "library schema is newer" in str(exc_info.value)


def test_a_composition_error_list_is_a_refusal_not_a_warning(tmp_path, monkeypatch):
    """R6, second half: ``compose_for_role`` reports a missing role into
    ``result.errors`` rather than raising — for this channel that is a refusal."""
    monkeypatch.setattr(
        check_resolve, "_library_roots", lambda _p: [_library(tmp_path / "lib", declares=True)]
    )
    monkeypatch.setattr(
        check_resolve,
        "_compose_role",
        lambda _p, _identity=None: _composition(errors=["Role 'ghost' not found"]),
    )

    with pytest.raises(CheckResolutionError) as exc_info:
        check_resolve.resolve_carried_checks(tmp_path, "rack")

    assert "Role 'ghost' not found" in str(exc_info.value)


def test_a_point_outside_the_kernel_topology_is_carried_and_then_skipped(tmp_path, monkeypatch):
    """ADR-0019 D11, replacing ``_guard_topology``.

    The carrier hands the row over — it cannot tell a typo from a point aimed at
    a sibling backlog — and the rack, which holds the running topology, does not
    subscribe it. Measured before: the abort took an UNRELATED edge down with it.
    """
    stray = _check(_script(tmp_path, "exit 0", name="stray.sh"), point="plan->execute")
    live = _check(_script(tmp_path, "exit 2", name="real.sh"), point="review->done")
    monkeypatch.setattr(
        check_resolve, "_library_roots", lambda _p: [_library(tmp_path / "lib", declares=True)]
    )
    monkeypatch.setattr(
        check_resolve,
        "_compose_role",
        lambda _p, _identity=None: _composition(checks=(stray, live)),
    )

    carried = check_resolve.resolve_carried_checks(tmp_path, "rack")
    assert [list(c.at) for c in carried] == [["plan->execute"], ["review->done"]]

    runner = _extension(
        tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology(), resolve=lambda: carried
    )
    # The stray edge fires nothing; the real one still refuses.
    assert runner.on_event(_ctx("open->review")) is None
    with pytest.raises(AbortOperation):
        runner.on_event(_ctx("review->done"))


def test_out_of_session_a_binding_resolves_live(tmp_path):
    """R5 / D9 clause 3: no ``AI_HATS_SESSION_ID`` (bare terminal, cron, the
    standalone binary) means live, never absent."""
    live = _script(tmp_path, "exit 0")

    resolved = check_resolve.resolve_carried_checks(
        tmp_path,
        "rack",
        compose=lambda _p: _composition(checks=(_check(live),)),
    )

    assert [c.script_path for c in resolved] == [live]


def test_in_session_a_binding_resolves_from_the_session_mirror(tmp_path):
    """R5 / D9 clause 2: a session runs from the copy it was launched with,
    isolated from the library it may be editing — the surface's skill mirror.

    The leaf is the composed skill's raw ``name`` (``quality::gates``), which is
    what every surface writes; HATS-1540 dropped the ``resolve_namespace``
    re-derivation that looked for ``quality/gates`` instead.
    """
    live = _script(tmp_path, "exit 0")
    mirrored_dir = _mirror_root(tmp_path, "sess-a") / "quality::gates"
    mirrored_dir.mkdir(parents=True)
    mirrored = _script(mirrored_dir, "exit 0")

    resolved = check_resolve.resolve_carried_checks(
        tmp_path,
        "rack",
        identity=_identity(tmp_path, "sess-a"),
        compose=lambda _p: _composition(checks=(_check(live),)),
    )

    assert [c.script_path for c in resolved] == [mirrored]


def test_in_session_a_missing_mirror_never_falls_back_to_the_live_path(tmp_path):
    """R10: silently re-resolving live would disarm the isolation the mirror
    exists for. The absent path stands, and ``run_hook`` calls it corruption."""
    live = _script(tmp_path, "exit 0")

    resolved = check_resolve.resolve_carried_checks(
        tmp_path,
        "rack",
        identity=_identity(tmp_path, "sess-a"),
        compose=lambda _p: _composition(checks=(_check(live),)),
    )

    assert resolved[0].script_path != live
    assert not resolved[0].script_path.exists()
    assert _mirror_root(tmp_path, "sess-a") in resolved[0].script_path.parents


def test_a_linked_worktree_is_never_a_resolution_root(tmp_path):
    """D9 clause 4: ``builtin_library_root`` is worktree-aware, so a naive
    resolve inside one runs the branch's own half-written check — a gate judging
    the change it is part of. The guard belongs to the resolver, not to a
    comment."""
    worktree = tmp_path / "ai-hats-wt-task-1"
    skill_dir = worktree / "libraries" / "skills" / "gates"
    skill_dir.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /repo/.git/worktrees/task-1\n")
    branch_copy = _script(skill_dir, "exit 0")

    with pytest.raises(CheckResolutionError) as exc_info:
        check_resolve.resolve_carried_checks(
            tmp_path,
            "rack",
            compose=lambda _p: _composition(checks=(_check(branch_copy),)),
        )

    assert str(worktree) in str(exc_info.value)


@pytest.mark.parametrize(
    "layout", ["plugin/skills", "rules/.agents/skills", "skills", "somewhere/else"]
)
def test_the_mirror_root_is_followed_verbatim_never_guessed(tmp_path, layout):
    """R3.1 / D9: each surface materializes skills where its own binary scans —
    ``<sid>/plugin/skills``, ``<sid>/rules/.agents/skills``, ``<sid>/skills``.

    A resolver keyed on one layout does nothing under the other two, so the root
    must be followed exactly as given. The fourth case is a layout no shipped
    surface uses: an out-of-tree one is served too.

    HATS-1594 moved WHO answers, not the invariant: the surface is asked once at
    launch (pinned in ``test_session_identity_launch.py``) and the answer rides
    the envelope. What is proven here is that the channel adds nothing to it.
    """  # comment-length: allow — which half of the invariant lives where
    root = ProjectLayout.at(tmp_path).cache.session("sess-a") / layout
    live = _script(tmp_path, "exit 0")
    mirrored_dir = root / "quality::gates"
    mirrored_dir.mkdir(parents=True)
    mirrored = _script(mirrored_dir, "exit 0")

    resolved = check_resolve.resolve_carried_checks(
        tmp_path,
        "rack",
        identity=_identity(tmp_path, "sess-a", skills_root=root),
        compose=lambda _p: _composition(checks=(_check(live),)),
    )

    assert [c.script_path for c in resolved] == [mirrored]
    source = Path(check_resolve.__file__).read_text()
    assert "plugin" not in source and ".agents" not in source


def test_the_check_budget_stays_under_the_rack_lock():
    """R9 / ADR-0020 D2: a hung check must be bounded by ITS timeout, not by the
    task lock — else a lock-waiting peer mis-blames a concurrent operation."""
    assert EDGE_CHECK_TIMEOUT_S < LOCK_TIMEOUT


# ---------------------------------------------------------------------------
# the declaration probe and the worktree guard, against real git
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@e")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "core.hooksPath", "/dev/null")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "commit", "-m", "init", "--allow-empty")
    return path


def test_the_probe_reads_exactly_the_roots_the_composition_reads(tmp_path, monkeypatch):
    """``Assembler`` re-points project-local ``libraries/`` into the linked
    worktree it is invoked from (HATS-831) while ``find_project_root`` hops back
    to MAIN. A probe deriving its own roots misses that one, so a binding that
    composes for real answers ``False`` here — a silently absent gate (R3)."""
    main = _repo(tmp_path / "proj")
    worktree = tmp_path / "ai-hats-wt-task-1"
    _git(main, "worktree", "add", "-b", "task/1", str(worktree))
    _library(worktree / "libraries", declares=True)
    monkeypatch.chdir(worktree)

    from ai_hats.assembler import Assembler

    assert check_resolve._library_roots(main) == Assembler(main).library_paths
    assert check_resolve.declares_checks(main) is True


def test_a_submodule_is_not_a_task_worktree(tmp_path):
    """A vendored component library carries a ``.git`` FILE too (``gitdir:
    …/.git/modules/…``). Refusing it names a worktree that does not exist, and
    since resolution precedes the per-edge filter it refuses every transition."""
    shared = _repo(tmp_path / "shared-lib")
    gate = _script(shared, "exit 0")
    _git(shared, "add", "-A")
    _git(shared, "commit", "-m", "lib")
    main = _repo(tmp_path / "proj")
    _git(main, "-c", "protocol.file.allow=always", "submodule", "add", str(shared), "vendor/shared")

    vendored = main / "vendor" / "shared" / gate.name
    assert (main / "vendor" / "shared" / ".git").is_file()

    resolved = check_resolve.resolve_carried_checks(
        main,
        "rack",
        compose=lambda _p: _composition(checks=(_check(vendored),)),
    )

    assert [c.script_path for c in resolved] == [vendored]


def test_a_real_linked_worktree_is_still_refused(tmp_path):
    """The other half of the same discrimination, on git's own output rather
    than a forged marker: ``gitdir: …/.git/worktrees/<id>`` still refuses."""
    main = _repo(tmp_path / "proj")
    worktree = tmp_path / "ai-hats-wt-task-1"
    _git(main, "worktree", "add", "-b", "task/1", str(worktree))
    branch_copy = _script(worktree, "exit 0")

    with pytest.raises(CheckResolutionError) as exc_info:
        check_resolve.resolve_carried_checks(
            main,
            "rack",
            compose=lambda _p: _composition(checks=(_check(branch_copy),)),
        )

    assert str(worktree) in str(exc_info.value)


def test_in_session_a_source_inside_a_worktree_is_refused_before_rebasing(tmp_path):
    """D9 clause 4, in the mode clause 2 owns. The guard ran on the REBASED
    path, which in a session is the cache root — outside any checkout, so the
    walk-up found no ``.git`` and the guard was dead. A snapshot copies whatever
    ``source_path`` points at, so a session started inside a worktree froze and
    ran that branch's bytes."""
    main = _repo(tmp_path / "proj")
    worktree = tmp_path / "ai-hats-wt-task-1"
    _git(main, "worktree", "add", "-b", "task/1", str(worktree))
    branch_copy = _script(worktree, "exit 0")
    mirrored_dir = _mirror_root(main, "sess-a") / "quality::gates"
    mirrored_dir.mkdir(parents=True)
    _script(mirrored_dir, "exit 0")

    with pytest.raises(CheckResolutionError) as exc_info:
        check_resolve.resolve_carried_checks(
            main,
            "rack",
            identity=_identity(tmp_path, "sess-a"),
            compose=lambda _p: _composition(checks=(_check(branch_copy),)),
        )

    assert str(worktree) in str(exc_info.value)


def test_the_probe_descends_into_a_symlinked_component_dir(tmp_path, monkeypatch):
    """``find_component_dir`` resolves a symlinked trait via ``is_dir()``, so a
    shared component wired in with a symlink composes for real. A scan that does
    not follow one skips its declaration in silence."""
    root = tmp_path / "lib"
    (root / "traits").mkdir(parents=True)
    shared = _library(tmp_path / "shared", declares=True)
    (root / "traits" / "maintainer").symlink_to(shared / "traits" / "maintainer")
    monkeypatch.setattr(check_resolve, "_library_roots", lambda _p: [root])

    assert check_resolve.declares_checks(tmp_path) is True


def test_a_symlink_cycle_does_not_hang_the_probe(tmp_path, monkeypatch):
    """Following symlinks buys the loop that comes with them."""
    root = _library(tmp_path / "lib", declares=False)
    (root / "traits" / "maintainer" / "loop").symlink_to(root / "traits")
    monkeypatch.setattr(check_resolve, "_library_roots", lambda _p: [root])

    assert check_resolve.declares_checks(tmp_path) is False


@pytest.mark.parametrize("spelling", ['"checks":', "'checks':", "checks :"])
def test_every_yaml_spelling_of_checks_is_seen_by_the_probe(tmp_path, monkeypatch, spelling):
    """The parser accepts quoted keys and space-before-colon; a substring test
    for ``checks:`` does not. Each miss is a gate that never installs."""
    root = tmp_path / "lib"
    trait = root / "traits" / "maintainer"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        f"name: maintainer\ncomposition:\n  {spelling}\n"
        f"    - skill: quality::gates\n      script: gate.sh\n"
        f"      on:\n        - review->done\n"
    )
    monkeypatch.setattr(check_resolve, "_library_roots", lambda _p: [root])

    assert yaml.safe_load((trait / "config.yaml").read_text())["composition"]["checks"]
    assert check_resolve.declares_checks(tmp_path) is True


def test_a_word_ending_in_checks_does_not_trip_the_probe(tmp_path, monkeypatch):
    """The other side of widening the match: ``composition.checks`` is a key at
    the start of its line, not any occurrence of the seven letters."""
    root = tmp_path / "lib"
    trait = root / "traits" / "maintainer"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text("name: maintainer\ndescription: runs prechecks: no\n")
    monkeypatch.setattr(check_resolve, "_library_roots", lambda _p: [root])

    assert check_resolve.declares_checks(tmp_path) is False


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads an unreadable directory anyway")
def test_an_unreadable_component_tree_is_loud_not_a_silent_false(tmp_path, monkeypatch):
    """Same class as the config parse error: a subtree the probe cannot read may
    hold the declaration, so ``False`` there is a guess, not an answer."""
    root = _library(tmp_path / "lib", declares=True)
    (root / "traits").chmod(0o000)
    monkeypatch.setattr(check_resolve, "_library_roots", lambda _p: [root])

    try:
        with pytest.raises(CheckResolutionError) as exc_info:
            check_resolve.resolve_carried_checks(tmp_path, "rack")
    finally:
        (root / "traits").chmod(0o755)

    assert "traits" in str(exc_info.value)


def test_a_check_cannot_outlive_the_task_lock_it_fires_in(tmp_path):
    """HATS-1603: the edge check used to mint a fresh EDGE_CHECK_TIMEOUT_S at its
    own t0, so a lock already spent still bought it a full budget past the lock's
    end. With the kernel's instant shipped, an exhausted lock leaves no budget."""
    script = _script(tmp_path, "exit 0")
    runner = _runner(tmp_path, _check(script))

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx(lock_expires_at=time.monotonic() - 1.0))

    assert "had no time left" in exc_info.value.reason
    assert "rack task lock" in exc_info.value.reason  # names the lock, not a constant


def test_a_check_under_a_live_lock_still_runs(tmp_path):
    """The clamp only shrinks: a lock with room left leaves the check its budget."""
    script = _script(tmp_path, "exit 0")
    runner = _runner(tmp_path, _check(script))

    assert runner.on_event(_ctx(lock_expires_at=time.monotonic() + 300.0)) is None
