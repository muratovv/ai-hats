from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import os
from pathlib import Path

import pytest
from ai_hats_library.hooks.consent_gate import Outcome, Verdict
from ai_hats_library.hooks.consent_gate.issue import DEFAULT_WINDOW_MINUTES

from ai_hats.consent_wrapper import (
    ConsentPolicyError,
    _is_consent_wrapper_path,
    WrapperConfig,
    match_operation,
    plan_consent,
    policy_of,
    run_wrapped,
)
from ai_hats.materialization import WriteKind, describe_mkdir
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surfaces.codex.provider import CodexSurface
from ai_hats.surfaces.plan import ExternalHook, Launch, MaterializationPlan
from tests._plan_helpers import composition_with


def _consent(operation: str | None, at: str, declared_by: str = "trait-agent") -> ExternalHook:
    return ExternalHook("consent_gate", operation, at, None, None, declared_by)


def _plan(tmp_path: Path, *hooks: ExternalHook) -> MaterializationPlan:
    import dataclasses

    composition = composition_with("r")
    composition = dataclasses.replace(
        composition, hooks=dataclasses.replace(composition.hooks, external=hooks)
    )
    root = tmp_path / "sessions" / "s1"
    return MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface="codex",
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=(describe_mkdir(root),),
        env={},
        launch=Launch(args=(), sdk_options=None),
    )


def test_exact_transition_selector_uses_source_state():
    policy = {"rack.transition": ("plan->execute",)}
    argv = ["transition", "HATS-1813", "execute"]

    assert match_operation("rack", argv, policy, source_state="review") is None
    assert match_operation("rack", argv, policy, source_state="plan") is not None


def test_rework_transition_delegates_after_source_resolution(tmp_path: Path):
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("plan->execute",)},
    )

    exit_code = run_wrapped(
        "rack",
        ["transition", "HATS-1813", "execute"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: pytest.fail("rework asked for consent"),
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        resolve_transition_source=lambda original, task_id, argv, environ: "review",
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 0
    assert spawned == [["/original/rack", "transition", "HATS-1813", "execute"]]


def test_source_resolution_failure_refuses_before_spawn(tmp_path: Path, capsys):
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("plan->execute",)},
    )

    def fail_resolution(*args):
        raise ConsentPolicyError("cannot resolve HATS-1813 source state")

    exit_code = run_wrapped(
        "rack",
        ["transition", "HATS-1813", "execute"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: pytest.fail("grant check ran"),
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        resolve_transition_source=fail_resolution,
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2
    assert spawned == []
    assert "cannot resolve HATS-1813 source state" in capsys.readouterr().err


def test_declared_rack_transition_refuses_before_spawn(tmp_path: Path):
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("plan->execute",)},
    )

    exit_code = run_wrapped(
        "rack",
        ["transition", "HATS-1755", "execute"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        resolve_transition_source=lambda original, task_id, argv, environ: "plan",
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2
    assert spawned == []


def test_refusal_names_executable_consent_operation(tmp_path: Path, capsys):
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("plan->execute",)},
    )

    exit_code = run_wrapped(
        "rack",
        ["transition", "HATS-1803", "execute"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        resolve_transition_source=lambda original, task_id, argv, environ: "plan",
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert f"consent rack.transition {DEFAULT_WINDOW_MINUTES}" in stderr
    assert "consent execute" not in stderr


def test_bad_config_refuses_recursive_wrapper_before_spawn(tmp_path: Path, capsys):
    wrapper = tmp_path / "outer" / "consent-wrapper" / "bin" / "rack"
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": str(wrapper)},
        policy={"rack.transition": ("plan->execute",)},
    )

    exit_code = run_wrapped(
        "rack",
        ["--help"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2
    assert spawned == []
    assert "refusing recursive wrapper target" in capsys.readouterr().err


def test_grant_starts_original_once_and_stays_outside_tool(tmp_path: Path):
    spawned: list[tuple[list[str], dict[str, str]]] = []
    recorded = []
    granted = Verdict(Outcome.GRANTED, grant_id="grant-a")
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("review->done",)},
    )

    exit_code = run_wrapped(
        "rack",
        ["transition", "HATS-1755", "--state", "done"],
        config,
        environ={"AI_HATS_CONSENT_TICKET": "ticket-a", "KEEP": "yes"},
        check_grant=lambda operation, target_dir: granted,
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        resolve_transition_source=lambda original, task_id, argv, environ: "review",
        record_grant=lambda answer, operation, project_dir: (
            recorded.append((answer, operation)) or True
        ),
        spawn=lambda command, environ: spawned.append((command, dict(environ))) or 0,
    )

    assert exit_code == 0
    assert spawned == [
        (["/original/rack", "transition", "HATS-1755", "--state", "done"], {"KEEP": "yes"})
    ]
    assert len(recorded) == 1
    assert recorded[0][0] is granted
    assert recorded[0][1].type == "rack.transition"


def test_grant_journal_failure_refuses_before_original_starts(tmp_path: Path):
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("review->done",)},
    )

    exit_code = run_wrapped(
        "rack",
        ["transition", "HATS-1755", "done"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: Verdict(Outcome.GRANTED, grant_id="grant-a"),
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        resolve_transition_source=lambda original, task_id, argv, environ: "review",
        record_grant=lambda answer, operation, project_dir: False,
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2
    assert spawned == []


def test_legacy_launch_ack_is_recorded_and_removed_before_spawn(tmp_path: Path):
    recorded: list[tuple[str, str]] = []
    spawned: list[dict[str, str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("plan->execute",)},
    )

    exit_code = run_wrapped(
        "rack",
        ["transition", "HATS-1755", "execute"],
        config,
        environ={"AI_HATS_PLAN_ACK": "1", "KEEP": "yes"},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        resolve_transition_source=lambda original, task_id, argv, environ: "plan",
        record_legacy=lambda flag, operation, project_dir: (
            recorded.append((flag, operation.type)) or True
        ),
        spawn=lambda command, environ: spawned.append(dict(environ)) or 0,
    )

    assert exit_code == 0
    assert recorded == [("AI_HATS_PLAN_ACK", "rack.transition")]
    assert spawned == [{"KEEP": "yes"}]


def test_ticket_must_be_consumed_before_original_starts(tmp_path: Path):
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("review->done",)},
    )

    exit_code = run_wrapped(
        "rack",
        ["transition", "HATS-1755", "done"],
        config,
        environ={"AI_HATS_CONSENT_TICKET": "ticket-a"},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv: True,
        consume_ticket=lambda task_id, argv: False,
        resolve_transition_source=lambda original, task_id, argv, environ: "review",
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2
    assert spawned == []


def test_mcp_ticket_journal_failure_prevents_execution(tmp_path: Path):
    from ai_hats.consent_wrapper import REQUEST_ENV

    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("->execute",)},
    )
    code = run_wrapped(
        "rack",
        ["transition", "HATS-001", "execute"],
        config,
        environ={REQUEST_ENV: "request-one"},
        check_grant=lambda *_: Verdict(Outcome.NO_AGENT, "no grant"),
        peek_ticket=lambda *_: True,
        consume_ticket=lambda *_: True,
        record_request=lambda *_: False,
        spawn=lambda *_: pytest.fail("Unjournaled authorization executed"),
    )
    assert code == 2


def test_declared_direct_wt_merge_refuses_before_spawn(tmp_path: Path):
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"ai-hats": "/original/ai-hats"},
        policy={"wt.merge": ("pre-merge",)},
    )

    exit_code = run_wrapped(
        "ai-hats",
        ["wt", "merge", "task/hats-1755"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv: False,
        consume_ticket=lambda task_id, argv: False,
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2
    assert spawned == []


def test_policy_compiles_nested_consent_gate_rows():
    rows = (
        _consent("rack.transition", "plan->execute"),
        _consent("rack.transition", "review->done"),
        _consent("wt.merge", "pre-merge"),
    )

    assert policy_of(rows) == {
        "rack.transition": ("plan->execute", "review->done"),
        "wt.merge": ("pre-merge",),
    }


@pytest.mark.parametrize(
    "selector, reason",
    [
        ("plan-execute", "an arrow"),
        ("edge:plan--execute", "an arrow"),
        ("review--done", "an arrow"),
        ("->", "both halves empty"),
        ("a->b->c", "exactly one"),
        ("review -> done", "no whitespace"),
        ("review->", "HATS-1720"),
        ("ANY->ANY", "HATS-1720"),
        ("NONE->execute", "HATS-1703"),
        ("ANY->done", "'->done'"),
        ("a->>b", "not a state name"),
        ("a-->b", "ends in '-'"),
    ],
)
def test_malformed_rack_transition_policy_fails_session_launch(selector, reason):
    with pytest.raises(ConsentPolicyError) as exc:
        policy_of((_consent("rack.transition", selector),))

    said = str(exc.value)
    assert selector in said
    assert reason in said


def test_unknown_consent_operation_fails_policy_compilation():
    with pytest.raises(ConsentPolicyError, match="unsupported consent operation 'rack.close'"):
        policy_of((_consent("rack.close", "review->done"),))


def test_nested_operation_path_fails_policy_compilation():
    with pytest.raises(ConsentPolicyError, match="exactly one operation key"):
        policy_of((_consent(None, "review->done"),))


def test_unknown_wt_merge_selector_fails_policy_compilation():
    with pytest.raises(ConsentPolicyError, match="only 'pre-merge'"):
        policy_of((_consent("wt.merge", "pre-merg"),))


def test_a_symlink_onto_an_inherited_wrapper_is_refused_at_planning(tmp_path: Path):
    """The host probe resolves links, so an alias in a clean-looking bin still
    names the wrapper it points at — and the consent planner refuses it."""
    wrapper = tmp_path / "outer" / "consent-wrapper" / "bin" / "rack"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("wrapper")
    alias = tmp_path / "canonical-bin" / "rack"
    alias.parent.mkdir()
    alias.symlink_to(wrapper)
    host = probe_host({"PATH": str(alias.parent)}, which=lambda name, path=None: str(alias))

    with pytest.raises(RuntimeError, match="resolved executable is a consent wrapper"):
        plan_consent(
            _plan(tmp_path, _consent("rack.transition", "review->done")),
            CodexSurface(),
            ProjectLayout.at(tmp_path),
            host,
        )


# --- HATS-1736 review: two pins the deleted in-tool tests used to carry --------


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["transition", "T-1", "done", "--force"], id="positional"),
        pytest.param(["transition", "T-1", "--state", "done", "--force"], id="state-flag"),
        pytest.param(["transition", "T-1", "--state=done", "--force"], id="state-equals"),
    ],
)
def test_force_does_not_switch_consent_off(tmp_path: Path, argv):
    """The HATS-1682 incident, re-pinned outside the tools.

    `--force` relaxes rack's FSM arrow; consent is a property of the OPERATION,
    so nothing added to the command line removes it. `rack_wiring` used to hold
    this pin and lost it with the in-lock subscriber (ADR-0030 D1) — the wrapper
    is where it belongs now, and until here nothing asserted it at all.
    """
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("plan->execute", "->done")},
    )

    exit_code = run_wrapped(
        "rack",
        argv,
        config,
        environ={},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv_: False,
        consume_ticket=lambda task_id, argv_: False,
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2, "a forced close walked past the question"
    assert spawned == [], "the tool ran before consent was settled"


@pytest.mark.parametrize("source", ["review", "plan", "document", "blocked", "failed"])
def test_every_road_into_done_carries_the_question(tmp_path: Path, source: str):
    """HATS-1752, translated. The card widened the declaration from `review->done`
    to `->done` because entering master must be asked about from ANY road; the
    guard that pinned it lived in the deleted contract test.

    The wrapper matches on the target state alone, so the pin is that a declared
    `->done` protects every source — and, by the parametrization above it, that a
    narrow spelling would not have been enough had the matcher ever looked at the
    source half.
    """
    del source  # the wrapper never sees it — that IS the property under test
    spawned: list[list[str]] = []
    config = WrapperConfig(
        project_dir=tmp_path,
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("plan->execute", "->done")},
    )

    exit_code = run_wrapped(
        "rack",
        ["transition", "T-1", "done"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv_: False,
        consume_ticket=lambda task_id, argv_: False,
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2, "a road into `done` was not gated"
    assert spawned == []


def test_a_declaration_that_names_no_done_leaves_the_road_open():
    """The discriminator for the pin above: without `->done` declared, the same
    command is delegated untouched. A test that passed either way would prove
    nothing about the widening."""
    config = WrapperConfig(
        project_dir=Path("/tmp"),
        originals={"rack": "/original/rack"},
        policy={"rack.transition": ("plan->execute",)},
    )
    spawned: list[list[str]] = []

    exit_code = run_wrapped(
        "rack",
        ["transition", "T-1", "done"],
        config,
        environ={},
        check_grant=lambda operation, target_dir: Verdict(Outcome.DENIED, "no grant"),
        peek_ticket=lambda task_id, argv_: False,
        consume_ticket=lambda task_id, argv_: False,
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 0 and spawned, "an undeclared target must pass through"


def test_the_guard_recognises_the_wrapper_the_planner_writes(tmp_path: Path):
    """HATS-1809: `_is_consent_wrapper_path` is the sole predicate under both
    recursion barriers, and it identifies a wrapper by the path shape that
    `plan_consent` spells independently. Both ends are derived from one real
    plan here — a test that built the path itself would only restate the
    literal it is supposed to guard."""
    canonical_bin = tmp_path / "canonical-bin"
    canonical_bin.mkdir()
    canonical = canonical_bin / "rack"
    canonical.write_text("original")
    host = probe_host({"PATH": str(canonical_bin)}, which=lambda name, path=None: str(canonical))

    armed = plan_consent(
        _plan(tmp_path, _consent("rack.transition", "review->done")),
        CodexSurface(),
        ProjectLayout.at(tmp_path),
        host,
    )

    wrapper = next(
        e.target
        for e in armed.entries
        if e.kind is WriteKind.WRITE_EXECUTABLE and e.target.name == "rack"
    )
    bin_dir = Path(armed.env["PATH"].split(os.pathsep)[0])

    assert _is_consent_wrapper_path(wrapper), (
        f"the guard does not recognise the wrapper the planner writes: {wrapper}"
    )
    assert _is_consent_wrapper_path(bin_dir), (
        f"the guard does not recognise the bin dir it put on PATH: {bin_dir}"
    )
