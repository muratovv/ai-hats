"""The consent declaration is a typed contract, not an untyped string (HATS-1682).

A row under ``composition.apps`` carries ``run:``, ``consent:`` or both. Once a
consent row can disarm a road into master, its point name is a security
boundary — and until this slice it was the one thing nothing type-checked: a
consent-only ``edge:plan-execute`` composed clean on every channel while the
identical row carrying ``run:`` was refused loudly.

Two contours, and they are deliberately different questions:

* **form** — is the name in the app's grammar at all? Answered at composition,
  fail-closed, because ai-hats can answer it without holding any topology;
* **topology** — does the name address a real edge of a real backlog? Answered
  by the rack, where the mounted topologies are, so a typo stays a finding and
  a point aimed at a sibling backlog stays a legal skip.

The landmine this must never re-arm: a row without ``run`` must reach neither
script resolution nor ``reject_worktree_root``.
"""  # comment-length: allow — which contour answers which question IS the fix

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core import (
    ComponentKind,
    CompositionResult,
    ConsentPoint,
    ResolvedCheck,
    ResolvedComponent,
)
from ai_hats_rack.checks import (
    CHECK_ROW,
    CONSENT_ROW,
    CheckSubscriber,
    classify_bindings,
)
from ai_hats_rack.dispatch import AbortOperation, DispatchContext
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.fsm import Topology
from ai_hats_rack.models import TaskCard

from ai_hats import check_resolve
from ai_hats.check_points import CheckBindingError, resolve_checks
from ai_hats.composer import _resolved_consent
from ai_hats.models import parse_app_bindings
from ai_hats.rack_consumers import AiHatsCheckPort

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIBRARY = _REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"

#: The rack topology these tests judge points against: `review -> done` exists,
#: `reviw -> done` does not, and neither does any `plan` state.
_TOPOLOGY = Topology(
    initial="open",
    states=("open", "review", "done"),
    edges={"open": ("review",), "review": ("done",), "done": ()},
)


def _rows(block, declared_by="trait-agent"):
    return list(parse_app_bindings(block, declared_by=declared_by))


def _consent_row(point: str, *, backlog: str = "tasks", declared_by: str = "trait-agent"):
    return _rows({"rack": {backlog: [{"at": [point], "consent": True}]}}, declared_by=declared_by)


def _port(*points: ConsentPoint, tmp_path: Path) -> AiHatsCheckPort:
    """The ai-hats port answering with these consent points and no gate rows."""
    return AiHatsCheckPort(
        tmp_path, catalog=tmp_path / "tasks", resolve_consent=lambda: tuple(points)
    )


def _point(name: str, *, backlog: str = "tasks", declared_by: str = "trait-agent") -> ConsentPoint:
    return ConsentPoint(declared_by=declared_by, app="rack", path=(backlog,), selector=name)


def _check(script: Path, *, point: str = "review->done") -> ResolvedCheck:
    return ResolvedCheck(
        app="rack",
        path=("tasks",),
        run=f"gates/{script.name}",
        at=(point,),
        cargo={},
        on_error="refuse",
        script_path=script,
        declared_by="maintainer",
    )


def _composed(*, checks=(), consent=()) -> CompositionResult:
    return CompositionResult(
        name="r",
        priorities=[],
        rules=[],
        skills=[],
        injections=[],
        checks=tuple(checks),
        consent=tuple(consent),
    )


def _subscriber(port, *, known_backlogs=("tasks", "hyp")) -> CheckSubscriber:
    return CheckSubscriber(port, topology=_TOPOLOGY, backlog="tasks", known_backlogs=known_backlogs)


def _ctx(event_key: str = "review->done") -> DispatchContext:
    src, dst = event_key.split("->")
    return DispatchContext(
        event=EdgeEvent(from_state=src, to_state=dst),
        task=TaskCard(id="T-1"),
        caller_cwd=Path.cwd(),
        is_epic=False,
        actor="test",
    )


# ----- contour 1: form, at composition, fail-closed -------------------------


#: Every branch of the grammar's refusal, reached through the ai-hats composition
#: contour — the fail-closed boundary A5 measured. Migrating this table by
#: leaving the retired spellings in place left all five hitting the SAME first
#: branch ("holds no arrow"), so a mutation that stopped judging arrows entirely
#: kept 3883 tests green (HATS-1719 review). Each row names the branch it reaches.
_MALFORMED = [
    ("plan-execute", "an arrow"),  # the measured A5 typo, respelt
    ("edge:plan--execute", "an arrow"),  # the RETIRED spelling is not an alias
    ("review--done", "an arrow"),  # no arrow at all
    ("->", "both halves empty"),  # a typo, not "everywhere"
    ("a->b->c", "exactly one"),  # more than one arrow
    ("review -> done", "no whitespace"),  # a second spelling of one selector
    ("NONE->execute", "HATS-1703"),  # reserved word, no call site yet
    ("ANY->done", "'->done'"),  # ANY on ONE side is a second spelling
    ("a->>b", "not a state name"),  # the natural mistyping of the arrow
    ("a-->b", "ends in '-'"),  # the other natural mistyping
]


@pytest.mark.parametrize("point, branch", _MALFORMED, ids=[p for p, _ in _MALFORMED])
def test_a_malformed_rack_consent_point_is_refused_at_composition(point, branch):
    """Fail-closed, because the alternative is what A5 measured: `plan-execute`
    composed with no error, no warning and nothing on any channel, and both roads
    into master were open.

    Asserted per BRANCH, not against one shared sentence: a table whose rows all
    reach the same refusal proves only that one refusal exists."""
    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(_consent_row(point), [])

    said = str(exc.value)
    assert repr(point) in said, "the refusal must name the point it refuses"
    assert "'trait-agent'" in said and "apps.rack" in said, f"the row is unnamed: {said}"
    assert branch in said, f"refused, but not by the branch this input reaches: {said}"


# ----- contour 1b: the VETO — not the name, but what the row DOES on it ------


#: The two wide-OUTPUT spellings. They left ``_MALFORMED`` in HATS-1720: the
#: grammar accepts them now, and what refuses them is the veto below — a
#: different contour, asserted per row KIND rather than per branch.
_WIDE_OUTPUT = ["execute->", "ANY->ANY"]


def _gate_row(point: str, *, declared_by: str = "maintainer"):
    return _rows(
        {"rack": {"tasks": [{"run": "gate-skill/hooks/gate.sh", "at": [point]}]}},
        declared_by=declared_by,
    )


@pytest.mark.parametrize("point", _WIDE_OUTPUT)
def test_a_gate_on_a_wide_output_is_refused_and_says_what_it_would_cost(point):
    """The measured consequence, not a style rule.

    A `run:` row is in-lock and may refuse, and a refusal on a wide output stands
    on EVERY way out of the state — `document`, `blocked`, `failed`, `cancelled`
    and the reclaim self-loop alike. The card is then locked where it is: measured,
    `on_error: warn` softens only a check that BROKE (`HookRun.downgradable`) and
    `--force` relaxes the FSM arrow while the check still runs. So the refusal has
    to name the cost, and name HATS-1723 — the card that makes a notify-only row
    legal here.
    """
    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(_gate_row(point), [])

    said = str(exc.value)
    assert repr(point) in said, "the refusal must name the selector it refuses"
    assert "locks the card" in said, f"the refusal does not say what it would cost: {said}"
    assert "HATS-1723" in said, f"the refusal does not name the card that relaxes it: {said}"


@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("point", _WIDE_OUTPUT)
def test_consent_on_a_wide_output_is_refused_whichever_way_it_speaks(point, value):
    """Both values, because both are dead config on this selector.

    `true` cannot work: the guard matches on the target state and never learns
    which state the card is leaving, so the row would reach it with no target and
    the question would never be asked — silently, the HATS-1682 A5 class. `false`
    switches off something nothing can switch on. Accepting either quietly is the
    silence this channel exists to remove; the form opens with HATS-1706.
    """
    row = _rows({"rack": {"tasks": [{"at": [point], "consent": value}]}})

    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(row, [])

    said = str(exc.value)
    assert repr(point) in said, "the refusal must name the selector it refuses"
    assert "HATS-1706" in said, f"the refusal does not name the card that opens it: {said}"


@pytest.mark.parametrize("point", ["->done", "review->done"])
def test_a_narrow_target_passes_the_veto_and_is_judged_on_its_merits(point):
    """The discriminator: a veto that refused every selector would pass the two
    tests above while disarming the shipped done-gate, which lives on `->done`.

    Reaching the skill lookup IS passing the veto — the row is refused for the
    missing skill, one contour further in.
    """
    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(_gate_row(point), [])

    said = str(exc.value)
    assert "composes no skill" in said, f"the row never reached script resolution: {said}"
    assert "HATS-1723" not in said and "HATS-1706" not in said


def test_the_veto_is_asked_before_the_script_exists():
    """Order matters: the wide-output row above names a skill nothing composes, and
    the veto still wins. A veto asked after resolution would let a well-composed
    role install the lock-in gate and only refuse the ones with typos."""
    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(_gate_row("execute->"), [])

    assert "composes no skill" not in str(exc.value)


def test_the_same_typo_is_refused_alike_with_and_without_a_script(tmp_path):
    """The asymmetry WAS the bug: an identical row carrying `run:` refused loudly
    and the consent-only one did not. One grammar, one verdict."""
    skill_dir = tmp_path / "gate-skill"
    (skill_dir / "hooks").mkdir(parents=True)
    script = skill_dir / "hooks" / "gate.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    skill = ResolvedComponent(
        name="gate-skill", component_type=ComponentKind.SKILL, source_path=skill_dir
    )
    gate = _rows(
        {"rack": {"tasks": [{"run": "gate-skill/hooks/gate.sh", "at": ["edge:plan-execute"]}]}}
    )

    with pytest.raises(CheckBindingError, match="a rack selector is an arrow"):
        resolve_checks(gate, [skill])
    with pytest.raises(CheckBindingError, match="a rack selector is an arrow"):
        resolve_checks(_consent_row("edge:plan-execute"), [skill])


def test_a_well_formed_point_for_an_edge_ai_hats_cannot_know_still_composes():
    """The form contour answers grammar only. Whether `plan` is a state of the
    backlog this row gates is the rack's question — ai-hats holds no topology,
    and refusing here would refuse every project whose backlog it cannot see."""
    (row,) = _consent_row("plan->execute")

    assert resolve_checks([row], []) == ()
    assert [c.selector for c in _resolved_consent([row])] == ["plan->execute"]


def test_a_foreign_app_keeps_its_own_grammar():
    """`_POINT_FORM` is keyed by app, so carrying a row for an application that
    ships no parser stays exactly as opaque as ADR-0019 D11 made it."""
    rows = _rows({"cronish": [{"at": ["every-tuesday"], "consent": True}]})

    assert resolve_checks(rows, []) == ()


# ----- contour 2: topology, where the mounted backlogs are ------------------


def test_a_consent_point_naming_an_edge_the_topology_lacks_is_reported_dead(tmp_path):
    """The half the form contour cannot answer, answered where it can be: the
    same `dead` mechanism a gate row's typo already gets (`rack doctor`)."""
    declarations = _port(_point("reviw->done"), tmp_path=tmp_path).check_declarations()

    (status,) = classify_bindings(declarations, {"tasks": _TOPOLOGY})

    assert status.status == "dead"
    assert "reviw->done" in status.detail
    assert "consent question" in status.detail, f"a consent row is not a gate: {status.detail}"


def test_a_live_consent_point_reports_armed(tmp_path):
    """The control: without it "dead" above could be how every consent row reads."""
    declarations = _port(_point("review->done"), tmp_path=tmp_path).check_declarations()

    (status,) = classify_bindings(declarations, {"tasks": _TOPOLOGY})

    assert (status.status, status.selector) == ("armed", "review->done")


def test_a_consent_point_aimed_at_a_sibling_backlog_is_not_an_error(tmp_path):
    """HATS-1545 R10 holds for the new kind too: a row addressed to a backlog
    this project really mounts is a SKIP, never a finding — otherwise every
    multi-backlog project would refuse on a row that was never its business.

    Both halves: the `tasks` subscriber passes the edge without a word, and the
    doctor, which sees every topology, calls the row healthy on its own backlog.
    """
    port = _port(_point("review->done", backlog="hyp"), tmp_path=tmp_path)

    assert _subscriber(port).on_event(_ctx()) is None, "a sibling's row must not fire here"

    (status,) = classify_bindings(port.check_declarations(), {"tasks": _TOPOLOGY, "hyp": _TOPOLOGY})
    assert status.status == "armed" and status.backlog == "hyp"


def test_a_point_belonging_to_a_sibling_topology_is_foreign_not_dead(tmp_path):
    """The distinction the whole classifier exists to draw: a point some OTHER
    mounted topology has is `foreign` (a legal skip), and only a point no
    topology anywhere has is `dead` (the finding). A consent row must not turn
    a sibling backlog's vocabulary into an error."""
    sibling = Topology(
        initial="new", states=("new", "active", "confirmed"), edges={"active": ("confirmed",)}
    )
    port = _port(_point("active->confirmed"), tmp_path=tmp_path)

    (status,) = classify_bindings(port.check_declarations(), {"tasks": _TOPOLOGY, "hyp": sibling})

    assert status.status == "foreign", "a sibling topology's point must not read as a typo"
    assert status.detail == "", "a legal skip carries no complaint"


def test_a_consent_point_on_a_backlog_nothing_answers_to_refuses_in_the_lock(tmp_path):
    """The other half of what A5 measured: `apps.rack.taks` (a typo'd backlog)
    reached no validator, so the point silently gated nothing. It now meets the
    same `_addresses_me` refusal a gate row has always met."""
    port = _port(_point("review->done", backlog="taks"), tmp_path=tmp_path)

    with pytest.raises(AbortOperation) as exc:
        _subscriber(port).on_event(_ctx())

    assert "taks" in str(exc.value) and "no backlog of this project answers" in str(exc.value)


def test_the_carried_consent_row_is_marked_as_one(tmp_path):
    """The discriminator, pinned: without it the rack cannot tell a declaration
    it must judge but never run from one it must run."""
    port = AiHatsCheckPort(
        tmp_path,
        catalog=tmp_path / "tasks",
        resolve_consent=lambda: (_point("review->done"),),
    )

    (declaration,) = port.check_declarations()

    assert declaration.kind == CONSENT_ROW
    assert declaration.at == ("review->done",)
    assert declaration.path == ("tasks",)
    assert declaration.on_error == "", "a row that spawns nothing has no failure policy"
    assert "declares consent" in declaration.label


# ----- the landmine: a row without `run` runs nothing and is rooted nowhere --


def test_a_consent_only_row_never_reaches_script_resolution():
    """Why the `continue` in ``resolve_checks`` exists. A consent row's ``run``
    is the empty string, so resolving one asks for a script inside a skill that
    was never named — and refuses the declaration for a defect it does not have.
    No skill is composed here on purpose: the row must not need one."""
    assert resolve_checks(_consent_row("review->done"), []) == ()
    assert resolve_checks(_rows({"wt": [{"at": ["pre-merge"], "consent": True}]}), []) == ()


def test_nothing_about_a_consent_point_can_be_rooted():
    """``reject_worktree_root`` judges the tree a SCRIPT resolves from, and it is
    reached only through ``result.checks``. A consent point is a different type
    with no path on it at all, so there is nothing for the clause to look at —
    the landmine is disarmed by the shape, not by a branch someone can delete."""
    assert not hasattr(ConsentPoint, "script_path")
    assert not hasattr(ConsentPoint, "source_path")


def test_a_consent_point_composed_inside_a_linked_worktree_still_resolves(tmp_path):
    """D9 clause 4, with the control beside it. A gate row whose script sits in
    a linked worktree is refused by the very same call; the consent point is
    not, because a declaration that spawns nothing has no bytes to run from the
    wrong tree (HATS-1682)."""
    worktree = tmp_path / "ai-hats-wt-task-1"
    skill_dir = worktree / "libraries" / "skills" / "gates"
    skill_dir.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /repo/.git/worktrees/task-1\n", encoding="utf-8")
    branch_copy = skill_dir / "gate.sh"
    branch_copy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    with pytest.raises(check_resolve.CheckResolutionError) as exc:
        check_resolve.resolve_carried_rows(
            tmp_path,
            "rack",
            identity=None,
            compose=lambda _p: _composed(checks=(_check(branch_copy),)),
        )
    assert str(worktree) in str(exc.value), "the control must prove the clause is live"

    checks, consent = check_resolve.resolve_carried_rows(
        tmp_path,
        "rack",
        identity=None,
        compose=lambda _p: _composed(consent=(_point("review->done"),)),
    )

    assert checks == ()
    assert [p.selector for p in consent] == ["review->done"]


def test_a_consent_row_is_never_handed_to_the_executor(tmp_path):
    """The last stop: the subscriber drops the kind before ``run_check``, so a
    declaration with no script can never be spawned."""

    class _Port:
        def check_declarations(self):
            return AiHatsCheckPort(
                tmp_path,
                catalog=tmp_path / "tasks",
                resolve_consent=lambda: (_point("review->done"),),
            ).check_declarations()

        def run_check(self, request):
            pytest.fail(f"the executor was asked to run {request.declaration.label}")

    assert _subscriber(_Port()).on_event(_ctx()) is None


def test_a_gate_row_on_the_same_edge_still_runs(tmp_path):
    """The control for the filter above: dropping the consent kind must not drop
    the check kind that shares its edge."""
    ran: list[str] = []

    class _Port:
        def check_declarations(self):
            from ai_hats_rack.checks import CheckDeclaration

            return (
                CheckDeclaration(
                    path=("tasks",),
                    at=("review->done",),
                    cargo={},
                    on_error="refuse",
                    label="a gate",
                    handle=None,
                    kind=CHECK_ROW,
                ),
                *AiHatsCheckPort(
                    tmp_path,
                    catalog=tmp_path / "tasks",
                    resolve_consent=lambda: (_point("review->done"),),
                ).check_declarations(),
            )

        def run_check(self, request):
            from ai_hats_rack.checks import CheckOutcome

            ran.append(request.declaration.label)
            return CheckOutcome(ok=True)

    _subscriber(_Port()).on_event(_ctx())

    assert ran == ["a gate"]


# ----- a flip between declarers is never silent (B10) -----------------------


def test_a_later_trait_switching_consent_off_warns(capsys):
    """B10: `_resolved_consent` is last-writer-wins, and the later writer need
    not be the role — an overlay appends a trait to the tail of the list. The
    RULE stays (the supervisor ruled a `false` wins); the silence does not."""
    first = _consent_row("review->done", declared_by="trait-agent")
    second = _rows(
        {"rack": {"tasks": [{"at": ["review->done"], "consent": False}]}},
        declared_by="trait-late",
    )

    assert _resolved_consent([*first, *second]) == (), "last-writer-wins must still hold"

    said = capsys.readouterr().err
    assert said.startswith("WARN:"), f"the flip was silent: {said!r}"
    assert "'trait-agent'" in said and "'trait-late'" in said
    assert "review->done" in said and "apps.rack.tasks" in said


def test_re_declaring_the_same_consent_is_idempotent_and_silent(capsys):
    """The card's own rule, and what makes the maintainer done-gate duplicate
    (Q3) free: a second `true` moves nothing and says nothing."""
    trait = _consent_row("review->done", declared_by="trait-agent")
    role = _rows(
        {"rack": {"tasks": [{"run": "g/d.sh", "at": ["review->done"], "consent": True}]}},
        declared_by="maintainer",
    )

    resolved = _resolved_consent([*trait, *role])

    assert [(c.selector, c.declared_by) for c in resolved] == [("review->done", "trait-agent")]
    assert capsys.readouterr().err == "", "an idempotent duplicate must be silent"


def test_arming_a_point_a_row_had_switched_off_does_not_warn(capsys):
    """Only the disarming direction is a finding — turning a gate ON is never
    the event that needs explaining."""
    off = _rows(
        {"rack": {"tasks": [{"at": ["review->done"], "consent": False}]}},
        declared_by="trait-a",
    )
    on = _consent_row("review->done", declared_by="trait-b")

    assert [c.selector for c in _resolved_consent([*off, *on])] == ["review->done"]
    assert capsys.readouterr().err == ""


# ----- a consent-only row is named by what it is (fix 3) --------------------


def test_a_consent_only_row_is_named_by_what_it_is():
    """``_label`` read ``run``, so a consent row printed "binds  under apps.wt"
    — a double space where the script would have been."""
    with pytest.raises(CheckBindingError) as exc:
        resolve_checks(_rows({"wt": [{"at": ["pre-merg"], "consent": True}]}), [])

    said = str(exc.value)
    assert "declares consent under apps.wt" in said
    assert "binds  under" not in said


def test_a_failure_policy_on_a_row_that_cannot_fail_is_refused():
    """The honest reading of the measured defect: `on_error: warn` on a
    consent-only wt row was refused for endangering data, by a row that spawns
    nothing. The key itself is the mistake, so it is refused where keys are read."""
    with pytest.raises(CheckBindingError) as exc:
        _rows({"wt": [{"at": ["pre-merge"], "consent": True, "on_error": "warn"}]})

    said = str(exc.value)
    assert "'on_error:'" in said and "no 'run:'" in said
    assert "protects data" not in said, f"the old, wrong reason survived: {said}"


def test_a_consent_only_row_at_a_real_owned_point_still_composes():
    """The control: refusing the KEY must not refuse the row."""
    rows = _rows({"wt": [{"at": ["pre-merge"], "consent": True}]})

    assert resolve_checks(rows, []) == ()
    assert [c.selector for c in _resolved_consent(rows)] == ["pre-merge"]


# ----- the entry point is exported like its siblings (B14) ------------------


def test_resolve_consent_points_is_on_the_module_surface():
    assert "resolve_consent_points" in check_resolve.__all__
    assert all(hasattr(check_resolve, name) for name in check_resolve.__all__)


# ----- the shipped library, as composed (Q2 / Q3) ---------------------------


def _shipped_consent(role: str) -> set[tuple[str, tuple[str, ...], str]]:
    """Every consent point ``role`` resolves to, against THIS checkout's library."""
    from ai_hats.assembler import Assembler

    asm = Assembler(_REPO_ROOT, library_paths=[_LIBRARY / "core", _LIBRARY / "usage"])
    result = asm.composer.compose(role)
    assert result.errors == [], result.errors
    return {(p.app, p.path, p.selector) for p in result.consent}


@pytest.mark.parametrize(
    "role",
    [
        "hypothesis-intake",
        "initial-wizard",
        "judge-auditor",
        "role-auditor",
        "role-judge",
        "session-reviewer",
    ],
)
def test_a_role_without_trait_agent_declares_no_consent(role):
    """Supervisor decision Q2, enforced rather than remembered. On master the
    `plan -> execute` gate was a property of the TOOL and fired for every role;
    it is a property of the role now, and these six do not carry it. The
    narrowing is intended — they do not drive a lifecycle — so it is pinned
    here, and a role that acquires the trait must change this list on purpose."""
    assert _shipped_consent(role) == set()


def test_a_lifecycle_role_does_declare_consent():
    """The control: an empty set above must mean "this role declares none", not
    "the probe reads nothing".

    The `consent_gate` rows answer a different question from the other three —
    they name the OPERATION TYPES a grant may cover, not where to ask — and they
    are pinned together so neither list can grow by accident (HATS-1735).
    """
    assert _shipped_consent("maintainer") == {
        ("consent_gate", (), "rack.transition"),
        ("consent_gate", (), "wt.merge"),
        ("rack", ("tasks",), "plan->execute"),
        ("rack", ("tasks",), "->done"),
        ("wt", (), "pre-merge"),
    }


def test_the_maintainer_gate_is_wide_and_the_question_no_longer_lags_it():
    """HATS-1752: the two reaches converged, so the role is back to ONE row.

    HATS-1719 split them because they genuinely differed — the gate covered
    every road into `done` while the question stayed on the review edge, a wide
    question costing a click per move (HATS-1728). HATS-1735 made one grant pay
    for a series, so the trait's question widened to `->done` and this role's
    second row declared nothing the trait does not. The question is not
    re-declared here: it belongs to every lifecycle role, and only this one owns
    the gate script.
    """  # comment-length: allow — why the row went away is the pin
    import yaml

    config = yaml.safe_load(
        (_LIBRARY / "usage/roles/maintainer/config.yaml").read_text(encoding="utf-8")
    )
    rows = config["composition"]["apps"]["rack"]["tasks"]

    (gate,) = rows
    assert gate["at"] == ["->done"], "the gate must cover every road into done"
    assert gate["on_error"] == "refuse"
    assert gate["run"].endswith("done-gate.sh")

    trait = yaml.safe_load((_LIBRARY / "core/traits/trait-agent/config.yaml").read_text("utf-8"))
    (question,) = trait["composition"]["apps"]["rack"]["tasks"]
    assert question["at"] == ["plan->execute", "->done"], (
        "the question rides every road into done, and `plan->execute` stays EXACT: "
        "a wide `->execute` would gate the rework loop"
    )
