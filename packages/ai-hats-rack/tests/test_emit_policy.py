"""HATS-1035 step 4: ``emit: always | when-set`` enforced at persist time.

The write layer (kernel single persist) hangs the emit gate off ``CardSchema``:
a declared field with ``emit: when-set`` and an empty value is dropped from the
persisted mapping — TaskCard.to_dict stays untouched. A parity pin holds the
packaged schema's emit declarations equal to to_dict's hardcoded behaviour for
the nine schema fields, and a golden round-trip proves byte identity.
"""

from __future__ import annotations

from ai_hats_rack.cardschema import CardSchema, ResolvedField, default_card_schema
from ai_hats_rack.models import TaskCard
from rack_testkit import make_kernel


def _when_set(name, ftype="str", default=""):
    return ResolvedField(name, ftype, True, default, False, None, None, "when-set")


# ----- emit_filter: drop empty when-set, keep always + set values ------------


def test_emit_filter_drops_empty_when_set_and_keeps_always():
    schema = default_card_schema()
    out = schema.emit_filter(TaskCard(id="T-1", title="x", description="").to_dict())
    assert "description" in out and "priority" in out  # emit: always (even empty)
    assert "resolution" not in out  # emit: when-set, empty → dropped
    assert "completed_at" not in out and "final_state" not in out


def test_emit_filter_keeps_a_set_when_set_field():
    schema = default_card_schema()
    out = schema.emit_filter(TaskCard(id="T-1", title="x", resolution="done").to_dict())
    assert out["resolution"] == "done"


def test_emit_filter_governs_an_extras_resident_schema_field():
    # A custom backlog's when-set field that is NOT a TaskCard column rides
    # extras — the gate drops/keeps it generically, off the final mapping.
    schema = CardSchema([_when_set("closed")])
    card = TaskCard(id="H-1", title="t")
    card.extras["closed"] = ""
    assert "closed" not in schema.emit_filter(card.to_dict())
    card.extras["closed"] = "2026-01-01T00:00:00Z"
    assert schema.emit_filter(card.to_dict())["closed"] == "2026-01-01T00:00:00Z"


def test_emit_filter_leaves_anchor_and_undeclared_keys_untouched():
    # An empty anchor field (final_state is declared, but depends_on is a link
    # anchor, NOT schema-driven) is never governed by a when-set schema entry.
    schema = CardSchema([_when_set("closed")])
    out = schema.emit_filter({"id": "T-1", "title": "", "unknown": ""})
    assert out == {"id": "T-1", "title": "", "unknown": ""}


# ----- parity pin: packaged emit declarations ≡ to_dict, the nine only -------


def test_packaged_emit_declarations_match_to_dict_for_the_nine():
    # Scoped to schema fields: to_dict's when-set for the LINK fields
    # (depends_on/related/see_also/folded_into/links) is NOT schema-driven.
    schema = default_card_schema()
    empty = {"description": "", "priority": "", "assignee": "", "reviewer": "",
             "role": "", "tags": [], "resolution": "", "completed_at": "", "final_state": ""}
    setv = {"description": "d", "priority": "p", "assignee": "a", "reviewer": "r",
            "role": "x", "tags": ["t"], "resolution": "res", "completed_at": "c",
            "final_state": "f"}
    assert {f.name for f in schema.fields} == set(empty)  # exactly the nine
    for f in schema.fields:
        d_empty = TaskCard(id="HATS-1", **{f.name: empty[f.name]}).to_dict()
        d_set = TaskCard(id="HATS-1", **{f.name: setv[f.name]}).to_dict()
        assert f.name in d_set, f"{f.name}: to_dict omits it even when set"
        behavior = "always" if f.name in d_empty else "when-set"
        assert behavior == f.emit, (
            f"drift: {f.name} to_dict is {behavior} but schema declares emit:{f.emit}"
        )


# ----- byte-identity golden round-trip ---------------------------------------


def test_golden_card_load_save_is_byte_identical(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)  # packaged tasks schema (zero-config)
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="demo",
                  description="a body", priority="high", role="dev", tags=["x", "y"])
    path = tasks_dir / "T-1" / "task.yaml"
    first = path.read_bytes()
    # load → persist through the emit gate is a fixed point (byte identity).
    kernel._persist(kernel.get("T-1"))
    assert path.read_bytes() == first
    text = first.decode("utf-8")
    assert "resolution:" not in text and "completed_at:" not in text  # when-set, empty
    assert "priority: high" in text and "description: a body" in text  # always
