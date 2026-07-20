"""``rack create`` — options GENERATED from the backlog ``fields[]`` (HATS-1036
R4/R7, ADR-0017 §7). Choices are NOT click.Choice — the schema enforces them
write-strict, so a bad value is the typed ``invalid_field`` refusal.

Exposed-input policy: schema fields MINUS the deny-lists below, PLUS the fixed
anchor/link inputs ``--id``/``--parent``/``--depends``; a list field is
repeatable, ``tags`` keeps ``--tag``. A ``route`` selects the kernel (tasks verb
→ wired/bare provider; a per-backlog group → its workspace instance).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import click

from ..cli_common import JSON_OPT, TASKS_DIR_OPT, actor, emit_json, handle_rack_error
from ..cli_kernel import _build_kernel, _echo_deltas, _provider, _result_payload
from ..definition import BacklogDefinition, FieldSpec
from ..kernel import Kernel, KernelResult
from ..resolver import RackRoot
from . import Verb

#: Written by stamp/clear-lifecycle handlers, never a create input.
_LIFECYCLE_OWNED = frozenset({"resolution", "completed_at", "final_state"})
#: No CLI input today + kernel.create takes no such kwarg → unexposed for byte-parity.
_CREATE_UNEXPOSED = frozenset({"assignee"})
#: The one historical singular-flag quirk: the ``tags`` list field → ``--tag``.
_FIELD_FLAG_ALIASES = {"tags": "--tag"}


@dataclass(frozen=True)
class CreateRoute:
    """How a ``create`` invocation reaches a kernel + wires the provider hooks.

    ``build`` resolves ``(kernel, root)`` from the caller's tasks-dir/cwd;
    ``after_create``/``handle_error`` are the wired-provider hooks (both ``None``
    on the portable per-backlog group path)."""

    build: Callable[["Path | None", Path], "tuple[Kernel, RackRoot]"]
    after_create: Callable[[RackRoot, KernelResult], None] | None
    handle_error: Callable[[Exception, bool], bool] | None


def _tasks_route() -> CreateRoute:
    """The tasks verb's route: the discovered wired kernel (integrator present) or
    the bare standalone kernel, with the provider's create hooks (HATS-1038 C1)."""
    provider = _provider()
    return CreateRoute(
        build=lambda tasks_dir, caller_cwd: _build_kernel(tasks_dir, caller_cwd, provider),
        after_create=(provider.after_create if provider is not None else None),
        handle_error=(provider.handle_error if provider is not None else None),
    )


def _exposed_create_fields(defn: BacklogDefinition) -> list[FieldSpec]:
    return [
        f
        for f in defn.fields
        if f.name not in _LIFECYCLE_OWNED and f.name not in _CREATE_UNEXPOSED
    ]


def _field_option(f: FieldSpec):
    """A create option for one exposed field: a repeatable option for a list
    field (schema default None-sentinel elsewhere), a scalar ``--<name>`` else."""
    if f.type == "list":
        return click.option(_FIELD_FLAG_ALIASES.get(f.name, f"--{f.name}"), f.name, multiple=True)
    return click.option(f"--{f.name}", default=None)


def _make_create_callback(scalar: tuple[str, ...], listy: tuple[str, ...], route_factory):
    def _create(**params) -> None:
        as_json = params["as_json"]
        # Exposed schema fields → the generic field mapping (list inputs materialized
        # from their option tuple); the kernel's schema resolves defaults, enforces
        # required/choices, and ignores any field absent from the routed schema.
        field_kwargs = {name: params[name] for name in scalar}
        field_kwargs.update({name: list(params[name]) for name in listy})
        caller_cwd = Path.cwd()
        route = route_factory()
        try:
            kernel, root = route.build(params["tasks_dir"], caller_cwd)
            result = kernel.create(
                actor=actor(),
                caller_cwd=caller_cwd,
                task_id=params["task_id"],
                title=params["title"],
                parent_task=params["parent_task"],
                depends_on=list(params["depends_on"]),
                fields=field_kwargs,
            )
        except Exception as exc:  # noqa: BLE001 — routed to typed handling
            if route.handle_error is not None and route.handle_error(exc, as_json):
                sys.exit(1)
            handle_rack_error(exc, as_json)
            return
        if route.after_create is not None:
            route.after_create(root, result)
        if as_json:
            emit_json(_result_payload(result))
        else:
            click.echo(f"Created: {result.task.id} [{result.task.state}] {result.task.title}")
            _echo_deltas(result)

    return _create


def build_create_command(
    defn: BacklogDefinition, *, route_factory: Callable[[], CreateRoute] | None = None
) -> click.Command:
    """Finalize ``create`` against ``defn`` — options generated from ``fields[]``.

    ``route_factory`` selects the kernel: the default is the tasks verb's
    wired/bare provider path; a per-backlog group passes a factory that routes to
    its workspace instance (HATS-1036 step 4)."""
    exposed = _exposed_create_fields(defn)
    scalar = tuple(f.name for f in exposed if f.type != "list")
    listy = tuple(f.name for f in exposed if f.type == "list")
    decorators = [
        click.argument("title"),
        click.option("--id", "task_id", default=None, help="Explicit id (default: allocate next)."),
        *[_field_option(f) for f in exposed],
        click.option("--parent", "parent_task", default="", help="Parent task id (epicifies the parent)."),
        click.option("--depends", "depends_on", multiple=True),
        TASKS_DIR_OPT,
        JSON_OPT,
    ]
    callback = _make_create_callback(scalar, listy, route_factory or _tasks_route)
    for deco in reversed(decorators):
        callback = deco(callback)
    return click.command(
        "create",
        help="Create a task card (initial state + field defaults come from backlog.yaml).",
    )(callback)


def verb() -> Verb:
    return Verb("create", build_create_command)
