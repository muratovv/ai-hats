"""The check runner that makes ``checks:`` fire on rack FSM edges (HATS-1141).

Covers the seam pack itself (subscription surface, slot 15, in-lock phase) and
the per-binding outcome policy of ADR-0019 D4 — the part ``worktree_hooks``
deliberately does NOT share, since that channel is uniformly fail-closed.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from ai_hats_core import ComponentKind, CompositionResult, ResolvedCheck, ResolvedComponent
from ai_hats_rack.dispatch import AbortOperation, DispatchContext, Phase
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.fsm import Topology, all_edge_keys
from ai_hats_rack.kernel import LOCK_TIMEOUT
from ai_hats_rack.models import TaskCard

from ai_hats import check_resolve
from ai_hats.check_points import resolve_checks
from ai_hats.check_resolve import CheckResolutionError
from ai_hats.models import CheckBinding
from ai_hats.paths import session_cache_dir
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

    log = tmp_path / "tasks" / "T-1" / ".checks" / "edge-review--done~quality+gates~gate.sh.log"
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


def test_an_unparseable_project_config_refuses_instead_of_tracebacking(tmp_path):
    """R8: the declaration probe reads ``ai-hats.yaml``, so its parse errors are
    this channel's errors. A project declaring NOTHING still reached
    ``ProjectConfig.from_yaml`` before the fail-closed boundary, and
    ``ProjectConfigError`` is neither ``OSError`` nor one of the typed two — so
    every transition ended in a stack trace."""
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 99\n")
    runner = CheckRunnerExtension(tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology())

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
    runner = CheckRunnerExtension(tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology())

    assert runner.on_event(_ctx()) is None


def test_a_role_that_is_set_but_unresolvable_still_refuses(tmp_path):
    """The other side of the line above: absence of a role is not a defect,
    a named role that does not compose is.

    The declaration is project-local on purpose. Reading it out of the ambient
    library made this test pass only while some shipped role happened to carry a
    ``checks:`` row — HATS-1538 withdrew that row and the test went red without
    the behaviour changing at all.
    """
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 1\nactive_role: ghost\n")
    declaring = tmp_path / "libraries" / "roles" / "declares"
    declaring.mkdir(parents=True)
    (declaring / "config.yaml").write_text(
        "name: declares\ncomposition:\n  checks:\n"
        "    - {skill: s, script: hooks/x.sh, on: ['edge:review--done']}\n"
    )
    assert check_resolve.declares_checks(tmp_path) is True, "precondition: probe must see it"
    runner = CheckRunnerExtension(tmp_path, tasks_dir=tmp_path / "tasks", topology=_topology())

    with pytest.raises(AbortOperation) as exc_info:
        runner.on_event(_ctx())

    assert "ghost" in exc_info.value.reason


# ---------------------------------------------------------------------------
# several bindings on one point (HATS-1137)
# ---------------------------------------------------------------------------


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
    assert sorted(logs) == [
        "edge-review--done~quality+gates~first.sh.log",
        "edge-review--done~quality+gates~second.sh.log",
    ]
    assert "first ran" in logs["edge-review--done~quality+gates~first.sh.log"]
    assert "second ran" not in logs["edge-review--done~quality+gates~first.sh.log"]
    assert "second ran" in logs["edge-review--done~quality+gates~second.sh.log"]


def test_the_log_name_carries_the_namespaced_skill_and_the_script_path(tmp_path):
    """The discriminator is the dedup identity — namespaced skill plus script —
    and both can carry separators (``dev::python``, ``hooks/done-gate.sh``), so
    both are escaped into one filename component that stays readable."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    check = ResolvedCheck(
        skill="dev::python",
        script="hooks/done-gate.sh",
        point="edge:review--done",
        on_error="refuse",
        script_path=_script(hooks, "echo 'nested ran'", name="done-gate.sh"),
        declared_by="maintainer",
    )

    assert _runner(tmp_path, check).on_event(_ctx()) is None

    logs = _logs(tmp_path)
    assert list(logs) == ["edge-review--done~dev+python~hooks+done-gate.sh.log"]
    assert "nested ran" in logs["edge-review--done~dev+python~hooks+done-gate.sh.log"]


def test_two_bindings_that_flatten_alike_still_get_two_logs(tmp_path):
    """Escaping, not replacing. A naive ``/`` → ``-`` would give ``a/b.sh`` and
    ``a-b.sh`` one filename, which is the very defect again — so the separator
    a script path collapses to is one no unescaped component can contain."""
    nested = tmp_path / "a"
    nested.mkdir()
    dashed = _check(_script(tmp_path, "echo 'dashed ran'", name="a-b.sh"))
    slashed = replace(_check(_script(nested, "echo 'slashed ran'", name="b.sh")), script="a/b.sh")

    assert _runner(tmp_path, slashed, dashed).on_event(_ctx()) is None

    logs = _logs(tmp_path)
    assert sorted(logs) == [
        "edge-review--done~quality+gates~a+b.sh.log",
        "edge-review--done~quality+gates~a-b.sh.log",
    ]
    assert "slashed ran" in logs["edge-review--done~quality+gates~a+b.sh.log"]
    assert "dashed ran" in logs["edge-review--done~quality+gates~a-b.sh.log"]


def test_retrying_the_edge_overwrites_that_bindings_own_log(tmp_path):
    """One file per (task, edge, binding): a retry replaces its own previous
    transcript rather than growing a pile beside the card."""
    runner = _runner(tmp_path, _check(_script(tmp_path, "echo 'first attempt'")))
    assert runner.on_event(_ctx()) is None

    _script(tmp_path, "echo 'second attempt'")  # same binding, new bytes
    assert runner.on_event(_ctx()) is None

    logs = _logs(tmp_path)
    assert list(logs) == ["edge-review--done~quality+gates~gate.sh.log"]
    assert "second attempt" in logs["edge-review--done~quality+gates~gate.sh.log"]
    assert "first attempt" not in logs["edge-review--done~quality+gates~gate.sh.log"]


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
    elsewhere = _check(
        _script(tmp_path, f"echo z >> '{order}'", name="z.sh"), point="edge:open--review"
    )
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
    assert list(_logs(tmp_path)) == ["edge-review--done~quality+gates~refuse.sh.log"]


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
    assert "ruff exploded" in logs["edge-review--done~quality+gates~broke.sh.log"]
    assert "second ran" in logs["edge-review--done~quality+gates~second.sh.log"]


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
    point = "edge:plan--execute"

    def row(script: str, on_error: str) -> CheckBinding:
        return CheckBinding.model_validate(
            {"skill": "gate-skill", "script": script, "on": (point,), "on_error": on_error}
        )

    resolved = resolve_checks(
        [
            ("trait-x", row("a.sh", "warn")),
            ("trait-y", row("b.sh", "refuse")),
            ("role-z", row("a.sh", "refuse")),
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
            "  checks:\n"
            "    - skill: quality::gates\n"
            "      script: gate.sh\n"
            "      on:\n"
            "        - edge:review--done\n"
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
    from ai_hats.paths import session_cache_dir

    return session_cache_dir(project_dir, session_id) / "mirror"


@pytest.fixture(autouse=True)
def _mirroring_surface(monkeypatch):
    """A surface whose skill mirror is one named dir — HATS-1540 resolves there.

    Autouse so every in-session case in this module reads one root; the real
    surfaces each scan a different relative path, which is exactly why the root
    is asked of the provider rather than guessed here.
    """
    from ai_hats import providers

    class _Mirroring:
        def session_skills_root(self, project_dir: Path, session_id: str) -> Path:
            return _mirror_root(project_dir, session_id)

    monkeypatch.setattr(providers, "get_provider", lambda _name: _Mirroring())
    yield


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

    resolved = check_resolve.resolve_edge_checks(
        tmp_path,
        topology=_topology(),
        session_id="sess-a",
        compose=lambda _p: _composition(checks=(_check(live),)),
    )

    assert [c.script_path for c in resolved] == [mirrored]


def test_in_session_a_missing_mirror_never_falls_back_to_the_live_path(tmp_path):
    """R10: silently re-resolving live would disarm the isolation the mirror
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
        check_resolve.resolve_edge_checks(
            tmp_path,
            topology=_topology(),
            compose=lambda _p: _composition(checks=(_check(branch_copy),)),
        )

    assert str(worktree) in str(exc_info.value)


@pytest.mark.parametrize(
    "layout", ["plugin/skills", "rules/.agents/skills", "skills", "somewhere/else"]
)
def test_the_mirror_root_is_asked_of_the_surface_never_guessed(tmp_path, monkeypatch, layout):
    """R3.1 / D9: each surface materializes skills where its own binary scans —
    ``<sid>/plugin/skills``, ``<sid>/rules/.agents/skills``, ``<sid>/skills``.

    HATS-1540 made that difference the ONLY thing the resolver asks a provider
    for, so the answer must follow whatever root the surface declares — a
    resolver keyed on one layout does nothing under the other two. The fourth
    case is a layout no shipped surface uses: an out-of-tree one is served too.
    """
    from ai_hats import providers

    root = session_cache_dir(tmp_path, "sess-a") / layout

    class _Elsewhere:
        def session_skills_root(self, project_dir, session_id):
            return root

    monkeypatch.setattr(providers, "get_provider", lambda _n: _Elsewhere())
    live = _script(tmp_path, "exit 0")
    mirrored_dir = root / "quality::gates"
    mirrored_dir.mkdir(parents=True)
    mirrored = _script(mirrored_dir, "exit 0")

    resolved = check_resolve.resolve_edge_checks(
        tmp_path,
        topology=_topology(),
        session_id="sess-a",
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

    resolved = check_resolve.resolve_edge_checks(
        main,
        topology=_topology(),
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
        check_resolve.resolve_edge_checks(
            main,
            topology=_topology(),
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
        check_resolve.resolve_edge_checks(
            main,
            topology=_topology(),
            session_id="sess-a",
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
        f"      on:\n        - edge:review--done\n"
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
            check_resolve.resolve_edge_checks(tmp_path, topology=_topology())
    finally:
        (root / "traits").chmod(0o755)

    assert "traits" in str(exc_info.value)
