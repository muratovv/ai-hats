"""HATS-1035 step 6: the ADR-0017 §5 HYP/PROP sketches, materialized as fixtures
and driven through loader + composition + write layer — proving both backlogs
are expressible WITHOUT engine edits.

Adjusted where the sketches name unbuilt machinery: ``hyp-quorum-gate`` /
``hyp-verdicts`` / ``prop-votes`` resolve to test stubs, the ``any``-typed
validators to test validators via the registry, ``targets:`` (cross-backlog,
HATS-1044) is dropped. The document anchor moved to composition-time
requires_states (ADR-0017 §3), so these document-less topologies compose.
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
from ai_hats_rack.composition import compose_subscribers, stock_factories
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.dispatch import Append, Delta, Set, bind_subscribers, validate_requires_states
from ai_hats_rack.kernel import Kernel
from rack_testkit import StubSubscriber, in_lock

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ----- stubs for the unbuilt handlers named in the sketches ------------------


class _StubExtension:
    """An ambient Subscriber that subscribes nothing (hyp-verdicts / prop-votes —
    their verbs are HATS-1044/T4 scope; here they only need to compose)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def subscriptions(self):
        return ()

    def on_event(self, ctx):
        return None


class _StubHandler:
    """A declaration-bound handler (name + on_event) for hyp-quorum-gate — the
    quorum machinery is HATS-1044; the gate only needs to bind on its edge."""

    def __init__(self, name: str) -> None:
        self.name = name

    def on_event(self, ctx):
        return None


def _factories():
    f = stock_factories()
    f["hyp-quorum-gate"] = lambda defn, catalog, cfg: _StubHandler("hyp-quorum-gate")
    f["hyp-verdicts"] = lambda defn, catalog, cfg: _StubExtension("hyp-verdicts")
    f["prop-votes"] = lambda defn, catalog, cfg: _StubExtension("prop-votes")
    return f


def _entry_list(value):
    if not isinstance(value, list):
        raise ValueError("expects a list of entries")
    if not all(isinstance(e, dict) for e in value):
        raise ValueError("each entry must be a mapping")


def _vote_entries(value):
    _entry_list(value)
    if not all("session" in e for e in value):
        raise ValueError("each vote needs a 'session'")


def _validators():
    return {
        "hyp-validation-log": _entry_list,
        "hyp-exit-criteria": lambda v: None,
        "prop-vote-entries": _vote_entries,
    }


def _build(catalog: Path, fixture: str, extra=()):
    defn = load_backlog(_FIXTURES / fixture)
    subs = list(compose_subscribers(defn, catalog, _factories())) + list(extra)
    validate_requires_states(subs, defn.topology, source=fixture)  # passes: no document anchor
    schema = build_card_schema(defn, _validators())
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
    return _build(catalog, "hyp-backlog.yaml", extra)


def _prop_kernel(catalog, extra=()):
    return _build(catalog, "prop-backlog.yaml", extra)


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
    hyp = load_backlog(_FIXTURES / "hyp-backlog.yaml")
    prop = load_backlog(_FIXTURES / "prop-backlog.yaml")
    assert "document" not in hyp.topology.states  # tasks-only state
    assert "document" not in prop.topology.states
    _hyp_kernel(tasks_dir / "h")  # composes (requires_states passes) — no raise
    _prop_kernel(tasks_dir / "p")
    assert hyp.topology.initial == "active"
    assert set(hyp.topology.states) == {"active", "confirmed", "refuted", "stalled"}
    assert prop.topology.initial == "open"
    assert {"accepted", "rejected", "deferred", "duplicate"} <= set(prop.topology.states)


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
    voter = _writer({"votes": Append({"session": "s1", "verdict": "yes"})}, "edge:open--accepted")
    kernel = _prop_kernel(tasks_dir, extra=[voter])
    _seed_prop(tasks_dir)
    card = kernel.transition("PROP-1", "accepted", actor="t", caller_cwd=cwd).task
    assert card.state == "accepted"
    assert card.extras["votes"] == [{"session": "s1", "verdict": "yes"}]


def test_prop_votes_validator_refuses_a_malformed_append(tasks_dir, cwd):
    voter = _writer({"votes": Append("not-a-dict")}, "edge:open--accepted")
    kernel = _prop_kernel(tasks_dir, extra=[voter])
    _seed_prop(tasks_dir)
    with pytest.raises(FieldValidationError):  # prop-vote-entries invoked on Append
        kernel.transition("PROP-1", "accepted", actor="t", caller_cwd=cwd)
    assert kernel.get("PROP-1").state == "open"
