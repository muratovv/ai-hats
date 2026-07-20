"""HATS-1036 step 2: the schema-generated `rack create` matches the historical
hand-written signature. The option SET (opts/dest/repeatable) is byte-parity
with the pre-refactor verb; choices stay on the kernel/schema layer (NOT a
click.Choice), so a bad value is the typed `invalid_field` refusal — not a
click usage error.
"""

from __future__ import annotations

import click

from ai_hats_rack.cli import main
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.verbs.create import build_create_command

# The pre-HATS-1036 `rack create` signature: (opts, dest, repeatable).
_HISTORICAL_OPTIONS = {
    (("--id",), "task_id", False),
    (("--description",), "description", False),
    (("--priority",), "priority", False),
    (("--role",), "role", False),
    (("--reviewer",), "reviewer", False),
    (("--parent",), "parent_task", False),
    (("--depends",), "depends_on", True),
    (("--tag",), "tags", True),
    (("--tasks-dir",), "tasks_dir", False),
    (("--json",), "as_json", False),
}


def _option_specs(cmd: click.Command) -> set[tuple]:
    return {
        (tuple(p.opts), p.name, bool(p.multiple))
        for p in cmd.params
        if isinstance(p, click.Option)
    }


def test_generated_option_set_equals_the_historical_signature():
    assert _option_specs(main.commands["create"]) == _HISTORICAL_OPTIONS


def test_title_stays_the_lone_positional_argument():
    args = [p for p in main.commands["create"].params if isinstance(p, click.Argument)]
    assert [p.name for p in args] == ["title"]


def test_choices_are_not_click_choice_so_the_error_stays_typed():
    # A click.Choice would make `--priority urgent` an exit-2 usage error; the
    # generated option keeps validation on the schema (typed invalid_field, exit 1).
    priority = next(p for p in main.commands["create"].params if p.name == "priority")
    assert not isinstance(priority.type, click.Choice)


def test_anchor_and_link_defaults_are_preserved():
    by_dest = {p.name: p for p in main.commands["create"].params}
    assert by_dest["task_id"].default is None  # --id: allocate-next sentinel
    assert by_dest["parent_task"].default == ""  # --parent: no-parent default


def test_lifecycle_owned_and_assignee_fields_are_not_exposed():
    # Deny-list holds: schema declares resolution/completed_at/final_state/assignee,
    # but none is a create input (byte-parity + kernel.create has no such kwargs).
    dests = {p.name for p in build_create_command(load_backlog()).params}
    assert dests.isdisjoint({"resolution", "completed_at", "final_state", "assignee"})
