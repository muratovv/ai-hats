from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from ai_hats_core import ConsentPoint
from ai_hats_library.hooks.consent_gate import Outcome, Verdict
from ai_hats_library.hooks.consent_gate.issue import DEFAULT_WINDOW_MINUTES

from ai_hats.consent_wrapper import (
    ConsentPolicyError,
    _is_consent_wrapper_path,
    WrapperConfig,
    match_operation,
    materialize_consent_wrappers,
    policy_from,
    run_wrapped,
)
from ai_hats.session_artifacts import BuiltArtifacts


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
    consent = wrapper_bin / "consent"
    assert consent.stat().st_mode & 0o111
    assert consent in artifacts.materialized
    assert (wrapper_bin / "rack").stat().st_mode & 0o111
    assert (wrapper_bin / "ai-hats").stat().st_mode & 0o111
    assert artifacts.extra_env["AI_HATS_CONSENT_WRAPPER_CONFIG"].endswith("config.json")


def test_materialization_skips_every_inherited_wrapper_bin(tmp_path: Path):
    inherited = [
        tmp_path / "outer" / "consent-wrapper" / "bin",
        tmp_path / "middle" / "consent-wrapper" / "bin",
    ]
    canonical_bin = tmp_path / "canonical-bin"
    for directory in (*inherited, canonical_bin):
        directory.mkdir(parents=True)
    canonical = canonical_bin / "rack"
    canonical.write_text("original")
    seen_paths: list[str] = []

    def resolve(name: str, path: str | None = None) -> str:
        assert name == "rack"
        seen_paths.append(path or "")
        return str(canonical)

    materialize_consent_wrappers(
        tmp_path,
        SimpleNamespace(
            consent=(
                ConsentPoint("trait-agent", "consent_gate", ("rack.transition",), "review->done"),
            )
        ),
        "sid-nested",
        SimpleNamespace(name="codex", supports_session_command_wrappers=lambda: True),
        BuiltArtifacts(),
        environ={
            "PATH": os.pathsep.join((str(inherited[0]), str(canonical_bin), str(inherited[1])))
        },
        which=resolve,
    )

    assert seen_paths == [str(canonical_bin)]


def test_materialization_rejects_wrapper_returned_by_custom_resolver(tmp_path: Path):
    wrapper = tmp_path / "outer" / "consent-wrapper" / "bin" / "rack"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("wrapper")

    with pytest.raises(RuntimeError, match="resolved executable is a consent wrapper"):
        materialize_consent_wrappers(
            tmp_path,
            SimpleNamespace(
                consent=(
                    ConsentPoint(
                        "trait-agent", "consent_gate", ("rack.transition",), "review->done"
                    ),
                )
            ),
            "sid-nested",
            SimpleNamespace(name="codex", supports_session_command_wrappers=lambda: True),
            BuiltArtifacts(),
            environ={"PATH": os.defpath},
            which=lambda name, path=None: str(wrapper),
        )


def test_materialization_rejects_symlink_to_inherited_wrapper(tmp_path: Path):
    wrapper = tmp_path / "outer" / "consent-wrapper" / "bin" / "rack"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("wrapper")
    alias = tmp_path / "canonical-bin" / "rack"
    alias.parent.mkdir()
    alias.symlink_to(wrapper)

    with pytest.raises(RuntimeError, match="resolved executable is a consent wrapper"):
        materialize_consent_wrappers(
            tmp_path,
            SimpleNamespace(
                consent=(
                    ConsentPoint(
                        "trait-agent", "consent_gate", ("rack.transition",), "review->done"
                    ),
                )
            ),
            "sid-nested",
            SimpleNamespace(name="codex", supports_session_command_wrappers=lambda: True),
            BuiltArtifacts(),
            environ={"PATH": str(alias.parent)},
            which=lambda name, path=None: str(alias),
        )


def test_materialization_fails_when_only_inherited_wrapper_exists(tmp_path: Path):
    wrapper_bin = tmp_path / "outer" / "consent-wrapper" / "bin"
    wrapper_bin.mkdir(parents=True)
    (wrapper_bin / "rack").write_text("wrapper")

    with pytest.raises(RuntimeError, match="executable not found on PATH"):
        materialize_consent_wrappers(
            tmp_path,
            SimpleNamespace(
                consent=(
                    ConsentPoint(
                        "trait-agent", "consent_gate", ("rack.transition",), "review->done"
                    ),
                )
            ),
            "sid-nested",
            SimpleNamespace(name="codex", supports_session_command_wrappers=lambda: True),
            BuiltArtifacts(),
            environ={"PATH": str(wrapper_bin)},
        )


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


def test_the_guard_recognises_the_wrapper_the_materializer_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """HATS-1809: `_is_consent_wrapper_path` is the sole predicate under both
    recursion barriers, and it identifies a wrapper by the path shape that
    `materialize_consent_wrappers` spells independently. Both ends are derived
    from one real materialization here — a test that built the path itself would
    only restate the literal it is supposed to guard, which is why the five
    HATS-1806 tests stay green when the producer is renamed."""
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    canonical_bin = tmp_path / "canonical-bin"
    canonical_bin.mkdir()
    canonical = canonical_bin / "rack"
    canonical.write_text("original")
    artifacts = BuiltArtifacts()

    materialize_consent_wrappers(
        tmp_path,
        SimpleNamespace(
            consent=(
                ConsentPoint("trait-agent", "consent_gate", ("rack.transition",), "review->done"),
            )
        ),
        "sid-1809",
        SimpleNamespace(name="codex", supports_session_command_wrappers=lambda: True),
        artifacts,
        environ={"PATH": str(canonical_bin)},
        which=lambda name, path=None: str(canonical),
    )

    wrapper = next(path for path in artifacts.materialized if path.name == "rack")
    bin_dir = Path(artifacts.extra_env["PATH"].split(os.pathsep)[0])

    assert _is_consent_wrapper_path(wrapper), (
        f"the guard does not recognise the wrapper the materializer wrote: {wrapper}"
    )
    assert _is_consent_wrapper_path(bin_dir), (
        f"the guard does not recognise the bin dir it put on PATH: {bin_dir}"
    )
