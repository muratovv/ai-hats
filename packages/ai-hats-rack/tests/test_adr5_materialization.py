"""The ADR-0017 §5 HYP/PROP definitions, driven through loader + composition +
write layer — proving both backlogs are expressible WITHOUT engine edits.

Re-pinned onto the SHIPPED packaged definitions (HATS-1044): the fixtures are
gone, the stubs replaced by the real stock factories/validators. This is the
executable contract for the packaged ``hypotheses``/``proposals`` backlog.yaml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_rack.cardschema import (
    ExtrasForbiddenError,
    FieldValidationError,
    RequiredFieldError,
    build_card_schema,
)
from ai_hats_rack.composition import compose_subscribers, stock_factories, stock_validators
from ai_hats_rack.definition import load_packaged_definition
from ai_hats_rack.dispatch import Append, Delta, Set, bind_subscribers, validate_requires_states
from ai_hats_rack.kernel import Kernel
from rack_testkit import StubSubscriber, in_lock


def _build(catalog: Path, name: str, extra=()):
    defn = load_packaged_definition(name)
    subs = list(compose_subscribers(defn, catalog, stock_factories())) + list(extra)
    validate_requires_states(subs, defn.topology, source=name)  # passes: no document anchor
    schema = build_card_schema(defn, stock_validators())
    kernel = Kernel(
        catalog,
        prefix=defn.prefix,
        topology=defn.topology,
        registry=defn.links_registry,
        edge_names=defn.edge_names,
        schema=schema,
        subscribers=subs,
    )
    bind_subscribers(subs, kernel)
    return kernel


def _hyp_kernel(catalog, extra=()):
    return _build(catalog, "hypotheses", extra)


def _prop_kernel(catalog, extra=()):
    return _build(catalog, "proposals", extra)


def _seed(catalog: Path, task_id: str, *, state: str, **fields) -> Path:
    """Write a raw task.yaml (reads are tolerant — the write policy never gates a
    seed) so a card with required fields exists to drive the write layer."""
    path = catalog / task_id / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"id: {task_id}", "title: seed", f"state: {state}"]
    lines += [f"{k}: {v}" for k, v in fields.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _writer(op, edge):
    return StubSubscriber("writer", [in_lock(edge)], action=lambda ctx: Delta(fields=op))


# ----- both topologies compose (the REQUIRED_STATES move) --------------------


def test_both_topologies_compose_without_document(tasks_dir):
    hyp = load_packaged_definition("hypotheses")
    prop = load_packaged_definition("proposals")
    assert "document" not in hyp.topology.states  # tasks-only state
    assert "document" not in prop.topology.states
    _hyp_kernel(tasks_dir / "h")  # composes (requires_states passes) — no raise
    _prop_kernel(tasks_dir / "p")
    assert hyp.topology.initial == "active"
    assert set(hyp.topology.states) == {"active", "confirmed", "refuted", "stalled"}
    assert prop.topology.initial == "open"
    assert {"accepted", "rejected", "deferred", "duplicate"} <= set(prop.topology.states)


# ----- packaged contract: cross-backlog targets + stored-inverse mirror ------


def test_hyp_targets_and_mirror_kinds_declared(tasks_dir):
    reg = load_packaged_definition("hypotheses").links_registry
    assert reg.get("source_task").targets == "tasks"
    assert reg.get("supersedes").inverse == "superseded_by"
    _hyp_kernel(tasks_dir)  # composes: mirror-link satisfies the stored-inverse rule


def test_prop_related_hypotheses_targets_hypotheses():
    reg = load_packaged_definition("proposals").links_registry
    assert reg.get("related_hypotheses").targets == "hypotheses"


# ----- HYP: required field, stamp-lifecycle {field: closed}, emit, validator -


def test_hyp_create_without_hypothesis_is_refused(tasks_dir, cwd):
    kernel = _hyp_kernel(tasks_dir)
    with pytest.raises(RequiredFieldError) as exc_info:
        kernel.create(actor="t", caller_cwd=cwd, task_id="HYP-1", title="a hypothesis card")
    assert exc_info.value.field_name == "hypothesis"
    assert not (tasks_dir / "HYP-1").exists()  # refused before any write


def test_hyp_confirm_stamps_closed_and_emit_when_set(tasks_dir, cwd):
    kernel = _hyp_kernel(tasks_dir)
    path = _seed(tasks_dir, "HYP-1", state="active", hypothesis="agents drift over long runs")
    assert "closed" not in path.read_text()  # emit when-set: absent before the stamp
    kernel.transition("HYP-1", "confirmed", actor="t", caller_cwd=cwd)
    card = kernel.get("HYP-1")
    assert card.state == "confirmed"
    assert card.extras["closed"]  # stamp-lifecycle {field: closed} wrote it
    assert "closed:" in path.read_text()  # emit when-set: present once stamped


def test_hyp_unstamped_edge_leaves_closed_absent(tasks_dir, cwd):
    kernel = _hyp_kernel(tasks_dir)
    path = _seed(tasks_dir, "HYP-1", state="active", hypothesis="h")
    kernel.transition("HYP-1", "stalled", actor="t", caller_cwd=cwd)  # no stamp on this edge
    assert "closed" not in path.read_text()
    assert "closed" not in kernel.get("HYP-1").extras


def test_hyp_any_field_validator_enforced_on_write(tasks_dir, cwd):
    bad = _writer({"validation_log": Set("not-a-list")}, "edge:active--stalled")
    kernel = _hyp_kernel(tasks_dir, extra=[bad])
    _seed(tasks_dir, "HYP-1", state="active", hypothesis="h")
    with pytest.raises(FieldValidationError, match="list"):
        kernel.transition("HYP-1", "stalled", actor="t", caller_cwd=cwd)
    assert kernel.get("HYP-1").state == "active"  # aborted before persist


# ----- PROP: extras forbid, category choices, votes validator on Append ------


def test_prop_create_without_category_is_refused(tasks_dir, cwd):
    kernel = _prop_kernel(tasks_dir)
    with pytest.raises(RequiredFieldError) as exc_info:
        kernel.create(actor="t", caller_cwd=cwd, task_id="PROP-1", title="a proposal")
    assert exc_info.value.field_name == "category"


def _seed_prop(catalog, task_id="PROP-1"):
    return _seed(
        catalog, task_id, state="open",
        category="rule", target="skill-x", description="d", rationale="r",
    )


def test_prop_category_choices_enforced_on_write(tasks_dir, cwd):
    bad = _writer({"category": Set("bogus")}, "edge:open--accepted")
    kernel = _prop_kernel(tasks_dir, extra=[bad])
    _seed_prop(tasks_dir)
    with pytest.raises(FieldValidationError) as exc_info:
        kernel.transition("PROP-1", "accepted", actor="t", caller_cwd=cwd)
    assert exc_info.value.details["choices"] == ["rule", "skill", "code", "process", "doc"]
    assert kernel.get("PROP-1").state == "open"


def test_prop_forbid_rejects_an_unknown_key_on_write(tasks_dir, cwd):
    bad = _writer({"mystery": Set("x")}, "edge:open--accepted")
    kernel = _prop_kernel(tasks_dir, extra=[bad])
    _seed_prop(tasks_dir)
    with pytest.raises(ExtrasForbiddenError) as exc_info:
        kernel.transition("PROP-1", "accepted", actor="t", caller_cwd=cwd)
    assert exc_info.value.field_name == "mystery"
    assert kernel.get("PROP-1").state == "open"


def test_prop_votes_validator_passes_a_valid_append(tasks_dir, cwd):
    vote = {"session_id": "s1", "timestamp": "2026-01-01T00:00:00Z", "reasoning": "sound"}
    voter = _writer({"votes": Append(vote)}, "edge:open--accepted")
    kernel = _prop_kernel(tasks_dir, extra=[voter])
    _seed_prop(tasks_dir)
    card = kernel.transition("PROP-1", "accepted", actor="t", caller_cwd=cwd).task
    assert card.state == "accepted"
    assert card.extras["votes"] == [vote]


def test_prop_votes_validator_refuses_a_malformed_append(tasks_dir, cwd):
    voter = _writer({"votes": Append("not-a-dict")}, "edge:open--accepted")
    kernel = _prop_kernel(tasks_dir, extra=[voter])
    _seed_prop(tasks_dir)
    with pytest.raises(FieldValidationError):  # prop-vote-entries invoked on Append
        kernel.transition("PROP-1", "accepted", actor="t", caller_cwd=cwd)
    assert kernel.get("PROP-1").state == "open"
