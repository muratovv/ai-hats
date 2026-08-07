"""The check runner that makes ``checks:`` fire on rack FSM edges (HATS-1141).

Covers the seam pack itself (subscription surface, slot 15, in-lock phase) and
the per-binding outcome policy of ADR-0019 D4 — the part ``worktree_hooks``
deliberately does NOT share, since that channel is uniformly fail-closed.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from ai_hats_core import CompositionResult, ResolvedCheck
from ai_hats_rack.dispatch import AbortOperation, DispatchContext, Phase
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.fsm import Topology, all_edge_keys
from ai_hats_rack.kernel import LOCK_TIMEOUT
from ai_hats_rack.models import TaskCard

from ai_hats import check_resolve
from ai_hats.check_resolve import CheckResolutionError
from ai_hats.paths import session_cache_dir, session_checks_dir
from ai_hats.rack_consumers import (
    CHECK_PRIORITY,
    EDGE_CHECK_TIMEOUT_S,
    CheckRunnerExtension,
    consumer_subscribers,
)


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


def _check(script: Path, *, point: str = "edge:review--done", on_error: str = "refuse"):
    return ResolvedCheck(
        skill="quality::gates",
        script=script.name,
        point=point,
        on_error=on_error,
        script_path=script,
        declared_by="maintainer",
    )


def _ctx(event_key: str = "edge:review--done", *, task_id: str = "T-1") -> DispatchContext:
    src, dst = event_key.removeprefix("edge:").split("--")
    return DispatchContext(
        event=EdgeEvent(from_state=src, to_state=dst),
        task=TaskCard(id=task_id),
        caller_cwd=Path.cwd(),
        is_epic=False,
        actor="test",
    )


def _runner(tmp_path: Path, *checks: ResolvedCheck, **kwargs) -> CheckRunnerExtension:
    return CheckRunnerExtension(
        tmp_path,
        tasks_dir=tmp_path / "tasks",
        topology=_topology(),
        resolve=lambda: tuple(checks),
        **kwargs,
    )


def test_pack_subscribes_to_every_edge_of_the_given_topology(tmp_path):
    """R2 + R7: the pack is no longer empty, it enumerates the topology handed
    through the seam (never a re-opened one), and it books slot 15 in-lock."""
    topology = _topology()
    pack = consumer_subscribers(tmp_path, tasks_dir=tmp_path / "tasks", topology=topology)

    assert pack, "the consumer pack must carry the check runner"
    subs = [spec for sub in pack for spec in sub.subscriptions()]
    assert {spec.event_key for spec in subs} == set(all_edge_keys(topology))
    assert {spec.phase for spec in subs} == {Phase.IN_LOCK}
    assert {spec.priority for spec in subs} == {15}
    assert CHECK_PRIORITY == 15


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

    assert "hook script missing" in exc_info.value.reason
    assert "quality::gates/vanished.sh" in exc_info.value.reason


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

    log = tmp_path / "tasks" / "T-1" / ".checks" / "edge-review--done.log"
    assert "all good" in log.read_text()


def test_a_resolution_failure_is_a_typed_refusal_not_a_traceback(tmp_path):
    """R8: the in-lock dispatcher re-raises anything that is not an
    ``AbortOperation`` verbatim, so an unconverted error reaches the user as a
    stack instead of a reason."""

    def boom() -> tuple[ResolvedCheck, ...]:
        raise CheckResolutionError("active role 'ghost' does not exist")

    runner = CheckRunnerExtension(
        tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology(), resolve=boom
    )

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert "active role 'ghost' does not exist" in exc_info.value.reason


# ---------------------------------------------------------------------------
# resolution (ADR-0019 D9) — two modes, one composition
# ---------------------------------------------------------------------------


def _library(root: Path, *, declares: bool) -> Path:
    trait = root / "traits" / "maintainer"
    trait.mkdir(parents=True)
    body = "name: maintainer\ncomposition:\n  skills:\n    - quality::gates\n"
    if declares:
        body += (
            "  checks:\n"
            "    - skill: quality::gates\n"
            "      script: gate.sh\n"
            "      on:\n"
            "        - edge:review--done\n"
        )
    (trait / "config.yaml").write_text(body)
    return root


def _composition(*, checks: tuple[ResolvedCheck, ...] = (), errors: list[str] | None = None):
    return CompositionResult(
        name="maintainer",
        priorities=[],
        rules=[],
        skills=[],
        injections=[],
        errors=errors or [],
        checks=checks,
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
        lambda _p: pytest.fail("composed a project that declares no checks"),
    )

    assert check_resolve.resolve_edge_checks(tmp_path, topology=_topology()) == ()


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
        lambda _p: (_ for _ in ()).throw(RuntimeError("library schema is newer")),
    )

    with pytest.raises(CheckResolutionError) as exc_info:
        check_resolve.resolve_edge_checks(tmp_path, topology=_topology())

    assert "library schema is newer" in str(exc_info.value)


def test_a_composition_error_list_is_a_refusal_not_a_warning(tmp_path, monkeypatch):
    """R6, second half: ``compose_for_role`` reports a missing role into
    ``result.errors`` rather than raising — for this channel that is a refusal."""
    monkeypatch.setattr(
        check_resolve, "_library_roots", lambda _p: [_library(tmp_path / "lib", declares=True)]
    )
    monkeypatch.setattr(
        check_resolve, "_compose_role", lambda _p: _composition(errors=["Role 'ghost' not found"])
    )

    with pytest.raises(CheckResolutionError) as exc_info:
        check_resolve.resolve_edge_checks(tmp_path, topology=_topology())

    assert "Role 'ghost' not found" in str(exc_info.value)


def test_a_point_outside_the_kernel_topology_names_both_topologies(tmp_path, monkeypatch):
    """R7: ``known_points`` validates against the PACKAGED tasks backlog while
    the kernel runs the catalog-local one. A gate that can never fire under the
    running topology is a silent skip — so it is named, loudly, on both sides."""
    stray = _check(_script(tmp_path, "exit 0"), point="edge:plan--execute")
    monkeypatch.setattr(
        check_resolve, "_library_roots", lambda _p: [_library(tmp_path / "lib", declares=True)]
    )
    monkeypatch.setattr(check_resolve, "_compose_role", lambda _p: _composition(checks=(stray,)))

    with pytest.raises(CheckResolutionError) as exc_info:
        check_resolve.resolve_edge_checks(tmp_path, topology=_topology())

    message = str(exc_info.value)
    assert "edge:plan--execute" in message
    assert "review" in message and "done" in message  # the topology the kernel holds
    assert "ai_hats_rack/backlog.yaml" in message  # the catalog composition validated against


def test_out_of_session_a_binding_resolves_live(tmp_path):
    """R5 / D9 clause 3: no ``AI_HATS_SESSION_ID`` (bare terminal, cron, the
    standalone binary) means live, never absent."""
    live = _script(tmp_path, "exit 0")

    resolved = check_resolve.resolve_edge_checks(
        tmp_path,
        topology=_topology(),
        compose=lambda _p: _composition(checks=(_check(live),)),
    )

    assert [c.script_path for c in resolved] == [live]


def test_in_session_a_binding_resolves_from_the_session_snapshot(tmp_path):
    """R5 / D9 clause 2: a session runs the same bytes start to finish, isolated
    from the library it may be editing — so the root is ai-hats's own snapshot."""
    live = _script(tmp_path, "exit 0")
    snapshot_dir = session_checks_dir(tmp_path, "sess-a") / "quality" / "gates"
    snapshot_dir.mkdir(parents=True)
    frozen = _script(snapshot_dir, "exit 0")

    resolved = check_resolve.resolve_edge_checks(
        tmp_path,
        topology=_topology(),
        session_id="sess-a",
        compose=lambda _p: _composition(checks=(_check(live),)),
    )

    assert [c.script_path for c in resolved] == [frozen]


def test_in_session_a_missing_snapshot_never_falls_back_to_the_live_path(tmp_path):
    """R10: silently re-resolving live would disarm the isolation the snapshot
    exists for. The absent path stands, and ``run_hook`` calls it corruption."""
    live = _script(tmp_path, "exit 0")

    resolved = check_resolve.resolve_edge_checks(
        tmp_path,
        topology=_topology(),
        session_id="sess-a",
        compose=lambda _p: _composition(checks=(_check(live),)),
    )

    assert resolved[0].script_path != live
    assert not resolved[0].script_path.exists()
    assert session_checks_dir(tmp_path, "sess-a") in resolved[0].script_path.parents


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
        check_resolve.resolve_edge_checks(
            tmp_path,
            topology=_topology(),
            compose=lambda _p: _composition(checks=(_check(branch_copy),)),
        )

    assert str(worktree) in str(exc_info.value)


def test_resolution_reads_no_provider_specific_path(tmp_path):
    """R3.1 / D9: each surface materializes skills where its own binary scans —
    ``<sid>/plugin/skills``, ``<sid>/rules/.agents/skills``, ``<sid>/skills``. A
    resolver keyed on any of them does nothing under the other two."""
    live = _script(tmp_path, "exit 0")
    snapshot_dir = session_checks_dir(tmp_path, "sess-a") / "quality" / "gates"
    snapshot_dir.mkdir(parents=True)
    frozen = _script(snapshot_dir, "exit 0")
    decoys = []
    for surface in ("plugin/skills", "rules/.agents/skills", "skills"):
        tree = session_cache_dir(tmp_path, "sess-a").joinpath(surface, "quality", "gates")
        tree.mkdir(parents=True)
        decoys.append(_script(tree, "exit 2"))

    def resolve():
        return check_resolve.resolve_edge_checks(
            tmp_path,
            topology=_topology(),
            session_id="sess-a",
            compose=lambda _p: _composition(checks=(_check(live),)),
        )

    assert [c.script_path for c in resolve()] == [frozen]

    for decoy in decoys:  # and the answer does not change when no surface tree exists
        shutil.rmtree(decoy.parent.parent.parent, ignore_errors=True)
    assert [c.script_path for c in resolve()] == [frozen]

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
