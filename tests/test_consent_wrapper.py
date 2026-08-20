from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from ai_hats_core import ConsentPoint
from ai_hats_library.hooks.consent_gate import Outcome, Verdict

from ai_hats.consent_wrapper import (
    ConsentPolicyError,
    WrapperConfig,
    materialize_consent_wrappers,
    policy_from,
    run_wrapped,
)
from ai_hats.session_artifacts import BuiltArtifacts


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
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2
    assert spawned == []


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
        spawn=lambda command, environ: spawned.append(command) or 0,
    )

    assert exit_code == 2
    assert spawned == []


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
    points = (
        ConsentPoint("trait-agent", "consent_gate", ("rack.transition",), "plan->execute"),
        ConsentPoint("trait-agent", "consent_gate", ("rack.transition",), "review->done"),
        ConsentPoint("trait-agent", "consent_gate", ("wt.merge",), "pre-merge"),
    )

    assert policy_from(points) == {
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
    point = ConsentPoint("trait-agent", "consent_gate", ("rack.transition",), selector)

    with pytest.raises(ConsentPolicyError) as exc:
        policy_from((point,))

    said = str(exc.value)
    assert selector in said
    assert reason in said


def test_unknown_consent_operation_fails_policy_compilation():
    point = ConsentPoint("trait-agent", "consent_gate", ("rack.close",), "review->done")

    with pytest.raises(ConsentPolicyError, match="unsupported consent operation 'rack.close'"):
        policy_from((point,))


def test_nested_operation_path_fails_policy_compilation():
    point = ConsentPoint(
        "trait-agent", "consent_gate", ("rack.transition", "tasks"), "review->done"
    )

    with pytest.raises(ConsentPolicyError, match="exactly one operation key"):
        policy_from((point,))


def test_unknown_wt_merge_selector_fails_policy_compilation():
    point = ConsentPoint("trait-agent", "consent_gate", ("wt.merge",), "pre-merg")

    with pytest.raises(ConsentPolicyError, match="only 'pre-merge'"):
        policy_from((point,))


def test_consent_outside_consent_gate_is_refused():
    point = ConsentPoint("legacy-role", "rack", ("tasks",), "review->done")

    with pytest.raises(ConsentPolicyError, match="apps.consent_gate"):
        policy_from((point,))


def test_materialization_wraps_declared_surfaces_in_session_path(tmp_path: Path):
    original_bin = tmp_path / "original-bin"
    original_bin.mkdir()
    originals = {name: original_bin / name for name in ("rack", "ai-hats")}
    for path in originals.values():
        path.write_text("original")
    result = SimpleNamespace(
        consent=(
            ConsentPoint("trait-agent", "consent_gate", ("rack.transition",), "review->done"),
            ConsentPoint("trait-agent", "consent_gate", ("wt.merge",), "pre-merge"),
        )
    )
    artifacts = BuiltArtifacts()

    materialize_consent_wrappers(
        tmp_path,
        result,
        "sid-a",
        SimpleNamespace(name="codex", supports_session_command_wrappers=lambda: True),
        artifacts,
        environ={"PATH": str(original_bin)},
        which=lambda name, path=None: str(originals[name]),
    )

    wrapper_bin = Path(artifacts.extra_env["PATH"].split(":", 1)[0])
    assert (wrapper_bin / "rack").stat().st_mode & 0o111
    assert (wrapper_bin / "ai-hats").stat().st_mode & 0o111
    assert artifacts.extra_env["AI_HATS_CONSENT_WRAPPER_CONFIG"].endswith("config.json")


def test_role_without_consent_keeps_the_original_command_surface(tmp_path: Path):
    artifacts = BuiltArtifacts()

    materialize_consent_wrappers(
        tmp_path,
        SimpleNamespace(consent=()),
        "sid-a",
        SimpleNamespace(name="agy", supports_session_command_wrappers=lambda: False),
        artifacts,
    )

    assert artifacts.extra_env == {}
    assert artifacts.materialized == []


def test_provider_without_command_interception_refuses_protected_role(tmp_path: Path):
    result = SimpleNamespace(
        consent=(ConsentPoint("trait-agent", "consent_gate", ("rack.transition",), "review->done"),)
    )

    with pytest.raises(RuntimeError, match="cannot enforce role-declared command consent"):
        materialize_consent_wrappers(
            tmp_path,
            result,
            "sid-a",
            SimpleNamespace(name="agy", supports_session_command_wrappers=lambda: False),
            BuiltArtifacts(),
        )
