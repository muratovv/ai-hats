"""Composition declares external command consent; tools do not consume it."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.check_points import resolve_checks
from ai_hats.composer import _resolved_consent
from ai_hats.consent_wrapper import policy_from
from ai_hats.models import parse_app_bindings

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIBRARY = _REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


def _rows(block, declared_by="trait-agent"):
    return list(parse_app_bindings(block, declared_by=declared_by))


def _consent_row(
    selector: str,
    *,
    operation: str = "rack.transition",
    value: bool = True,
    declared_by: str = "trait-agent",
):
    return _rows(
        {"consent_gate": {operation: [{"at": [selector], "consent": value}]}},
        declared_by=declared_by,
    )


def test_consent_gate_rows_become_wrapper_policy_not_executable_checks():
    rows = [
        *_consent_row("plan->execute"),
        *_consent_row("review->done"),
        *_consent_row("pre-merge", operation="wt.merge"),
    ]

    points = _resolved_consent(rows)

    assert resolve_checks(rows, []) == ()
    assert policy_from(points) == {
        "rack.transition": ("plan->execute", "review->done"),
        "wt.merge": ("pre-merge",),
    }


def test_a_later_trait_switching_wrapper_consent_off_warns(capsys):
    first = _consent_row("review->done", declared_by="trait-agent")
    second = _consent_row("review->done", value=False, declared_by="trait-late")

    assert _resolved_consent([*first, *second]) == ()

    said = capsys.readouterr().err
    assert said.startswith("WARN:")
    assert "'trait-agent'" in said and "'trait-late'" in said
    assert "review->done" in said and "apps.consent_gate.rack.transition" in said


def test_redeclaring_the_same_wrapper_consent_is_idempotent_and_silent(capsys):
    trait = _consent_row("review->done", declared_by="trait-agent")
    role = _consent_row("review->done", declared_by="maintainer")

    resolved = _resolved_consent([*trait, *role])

    assert [(point.selector, point.declared_by) for point in resolved] == [
        ("review->done", "trait-agent")
    ]
    assert capsys.readouterr().err == ""


def test_arming_a_wrapper_point_after_false_does_not_warn(capsys):
    off = _consent_row("review->done", value=False, declared_by="trait-a")
    on = _consent_row("review->done", declared_by="trait-b")

    assert [point.selector for point in _resolved_consent([*off, *on])] == ["review->done"]
    assert capsys.readouterr().err == ""


def _shipped_consent(role: str) -> set[tuple[str, tuple[str, ...], str]]:
    from ai_hats.assembler import Assembler

    assembler = Assembler(_REPO_ROOT, library_paths=[_LIBRARY / "core", _LIBRARY / "usage"])
    result = assembler.composer.compose(role)
    assert result.errors == [], result.errors
    return {(point.app, point.path, point.selector) for point in result.consent}


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
def test_a_role_without_trait_agent_declares_no_wrapper_consent(role):
    assert _shipped_consent(role) == set()


def test_a_lifecycle_role_declares_only_external_wrapper_consent():
    assert _shipped_consent("maintainer") == {
        ("consent_gate", ("rack.transition",), "plan->execute"),
        # HATS-1752: every road into `done`, not the review edge alone.
        ("consent_gate", ("rack.transition",), "->done"),
        ("consent_gate", ("wt.merge",), "pre-merge"),
    }


def test_the_maintainer_gate_does_not_own_consent():
    import yaml

    config = yaml.safe_load(
        (_LIBRARY / "ai-hats-dev/roles/maintainer/config.yaml").read_text(encoding="utf-8")
    )
    (gate,) = config["composition"]["apps"]["rack"]["tasks"]

    assert gate["at"] == ["->done"]
    assert gate["on_error"] == "refuse"
    assert "consent" not in gate


def test_the_shipped_question_rides_every_road_into_done():
    """HATS-1752, translated into the wrapper's declaration (HATS-1736 review).

    The card widened the question from `review->done` to `->done` because master
    must be asked about from ANY road, the forced close included. The guard that
    pinned it read `apps.rack.tasks` and went with those rows in ADR-0030; the
    declaration now lives under `apps.consent_gate`, and without this the next
    narrowing would pass every gate — the wrapper matches on the target alone, so
    a narrow spelling breaks nothing today and everything the day a selector-aware
    matcher arrives.
    """
    import yaml

    trait = yaml.safe_load(
        (_LIBRARY / "core/traits/trait-agent/config.yaml").read_text(encoding="utf-8")
    )
    declared = trait["composition"]["apps"]["consent_gate"]

    (question,) = declared["rack.transition"]
    assert question["at"] == ["plan->execute", "->done"], (
        "the question must ride every road into `done`; `plan->execute` stays EXACT "
        "because entering implementation is one edge, not a family"
    )
    assert question["consent"] is True

    gate = yaml.safe_load(
        (_LIBRARY / "ai-hats-dev/roles/maintainer/config.yaml").read_text(encoding="utf-8")
    )
    (gate_row,) = gate["composition"]["apps"]["rack"]["tasks"]
    assert gate_row["at"] == ["->done"], "the gate and the question must cover one set"
    assert gate_row["run"].endswith("done-gate.sh")


def test_the_shipped_declaration_compiles_to_a_policy_that_protects_done():
    """The other half: the YAML above must survive the wrapper's own compiler and
    come out protecting `done`, not merely mentioning it."""
    trait = _rows(
        {
            "consent_gate": {
                "rack.transition": [{"at": ["plan->execute", "->done"], "consent": True}],
                "wt.merge": [{"at": ["pre-merge"], "consent": True}],
            }
        }
    )
    sink: list = []

    policy = policy_from(_resolved_consent(trait, sink))

    assert "->done" in policy["rack.transition"]
    assert policy["wt.merge"] == ("pre-merge",)
