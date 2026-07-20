"""Per-backlog command groups (HATS-1036 R2/R5, ADR-0017 §4/§7).

Every NON-tasks backlog the workspace mounts becomes a group named by its
declared ``cli_alias`` (its ``name`` when unset), carrying a schema-driven
``create``, an ``update`` sugar (schema options → ``--set`` field ops), and the
verbs its extensions contribute through the optional ``verbs()`` hook. Groups
are discovered LAZILY from the ambient root, so the base four-verb surface is
unchanged until sibling catalogs are mounted (R2). This layer stays 100%
backlog-agnostic: a backlog's short CLI name is part of ITS definition.
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from ..cli_common import (
    ENV_TASKS_DIR,
    JSON_OPT,
    TASKS_DIR_OPT,
    actor,
    emit_json,
    fail,
    handle_rack_error,
    resolved_root,
)
from ..cli_kernel import _echo_deltas, _result_payload
from ..composition import compose_subscribers, stock_factories
from ..definition import BacklogDefinition
from ..ops import parse_ops
from ..resolver import NoProjectRootError, resolve_root
from ..workspace import BacklogInstance, Workspace, WorkspaceError
from .create import CreateRoute, _LIFECYCLE_OWNED, build_create_command


class DuplicateGroupNameError(WorkspaceError):
    """Two mounted non-tasks backlogs resolve to the SAME CLI group name (a
    ``cli_alias`` or a ``name``): the top-level surface cannot mount both, so it
    fails closed rather than shadow one (HATS-1036)."""

    def __init__(self, group: str, names: tuple[str, str]) -> None:
        self.group = group
        self.names = names
        super().__init__(
            f"CLI group name {group!r} is claimed by more than one backlog "
            f"{list(names)} — set a distinct 'cli_alias' on one"
        )


def group_name(instance: BacklogInstance) -> str:
    """The CLI group name for a mounted backlog: its declared ``cli_alias`` or,
    absent one, its ``name``."""
    return instance.definition.cli_alias or instance.name


# ----- update sugar ----------------------------------------------------------


def build_update_command(defn: BacklogDefinition) -> click.Command | None:
    """``rack <group> update <ID> --<field> <value>`` — scalar field edits mapped
    onto the same ``--set``/FieldsOp path as ``transition`` (R3). Only scalar
    (str/int) non-lifecycle fields are exposed; list/complex fields are appended
    through ``transition --append``. ``None`` when the backlog has no such field."""
    scalars = [
        f for f in defn.fields if f.type in ("str", "int") and f.name not in _LIFECYCLE_OWNED
    ]
    if not scalars:
        return None
    field_types = {f.name: f.type for f in defn.fields}
    names = tuple(f.name for f in scalars)

    def _update(**params) -> None:
        as_json = params["as_json"]
        task_id = params["task_id"]
        provided = {name: params[name] for name in names if params[name] is not None}
        if not provided:
            fail(as_json, "invalid_request", "update needs at least one --<field> to set")
            return
        caller_cwd = Path.cwd()
        try:
            # Ride the transition --set path verbatim (one FieldsOp per field): int
            # coercion + typed refusals come from parse_ops, one mutating verb (R6).
            tokens: list[str] = []
            for name, raw in provided.items():
                tokens += ["--set", f"{name}={raw}"]
            ops = parse_ops(tokens, field_types=field_types)
            root = resolved_root(params["tasks_dir"], caller_cwd)
            workspace = Workspace.discover([root])
            result = workspace.kernel_for(task_id).transition_ops(
                task_id, ops, actor=actor(), caller_cwd=caller_cwd
            )
        except Exception as exc:  # noqa: BLE001 — routed to typed handling
            handle_rack_error(exc, as_json)
            return
        if as_json:
            emit_json(_result_payload(result))
        else:
            click.echo(f"Updated: {result.task.id} {', '.join(sorted(provided))}")
            _echo_deltas(result)

    decorators = [
        click.argument("task_id"),
        *[click.option(f"--{name}", default=None) for name in names],
        TASKS_DIR_OPT,
        JSON_OPT,
    ]
    callback = _update
    for deco in reversed(decorators):
        callback = deco(callback)
    return click.command("update", help="Edit scalar card fields (schema-validated --set ops).")(
        callback
    )


# ----- per-backlog group assembly --------------------------------------------


def _group_create_route_factory(prefix: str):
    """A ``create`` route that writes to the backlog ``prefix`` routes to — its
    workspace instance (portable kit + declared extensions), not the tasks kernel."""

    def factory() -> CreateRoute:
        def build(tasks_dir, caller_cwd):
            root = resolved_root(tasks_dir, caller_cwd)
            workspace = Workspace.discover([root])
            # Route by prefix — the create has no id yet, so a synthetic one selects
            # the instance (instance_for is prefix-keyed; existence is never checked).
            instance = workspace.instance_for(f"{prefix}-0")
            return workspace.kernel_for_instance(instance), root

        return CreateRoute(build=build, after_create=None, handle_error=None)

    return factory


def build_backlog_group(instance: BacklogInstance) -> click.Group:
    """Assemble one backlog's group: schema-driven create + update sugar + the
    verbs its extensions contribute via ``verbs()`` (R5, ADR-0017 §4)."""
    group = click.Group(
        name=group_name(instance),
        help=f"{instance.name} backlog ({instance.prefix}-… cards).",
    )
    group.add_command(
        build_create_command(
            instance.definition, route_factory=_group_create_route_factory(instance.prefix)
        )
    )
    update_cmd = build_update_command(instance.definition)
    if update_cmd is not None:
        group.add_command(update_cmd)
    for sub in compose_subscribers(instance.definition, instance.catalog, stock_factories()):
        verbs = getattr(sub, "verbs", None)
        if verbs is None:
            continue
        for cmd in verbs():
            group.add_command(cmd)
    return group


# ----- dynamic top-level surface ---------------------------------------------


def _ambient_workspace() -> Workspace | None:
    """The workspace at the ambient root (``RACK_TASKS_DIR`` / cwd), or ``None``
    when there is no project or discovery fails — groups then simply do not
    appear, the base surface stands alone (R2). Fail-soft: group discovery must
    never brick the base CLI."""
    override = os.environ.get(ENV_TASKS_DIR)
    try:
        root = resolve_root(Path.cwd(), Path(override) if override else None)
        return Workspace.discover([root])
    except NoProjectRootError:
        return None
    except Exception:  # noqa: BLE001 — a malformed sibling never bricks --help
        return None


def _mounted_groups(workspace: Workspace) -> dict[str, BacklogInstance]:
    """Each non-tasks instance keyed by its CLI group name; two backlogs resolving
    to the same name is a fail-closed :class:`DuplicateGroupNameError` (HATS-1036)."""
    groups: dict[str, BacklogInstance] = {}
    for inst in workspace.instances:
        if inst.is_tasks:
            continue
        name = group_name(inst)
        if name in groups:
            raise DuplicateGroupNameError(name, (groups[name].name, inst.name))
        groups[name] = inst
    return groups


class RackGroup(click.Group):
    """``main`` as a lazy group: the four base verbs live in ``.commands``; the
    per-backlog groups are resolved on demand from the ambient workspace, so the
    surface stays exactly the base four until sibling catalogs are mounted (R2)."""

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        cmd = super().get_command(ctx, name)
        if cmd is not None:
            return cmd
        workspace = _ambient_workspace()
        if workspace is None:
            return None
        inst = _mounted_groups(workspace).get(name)
        return build_backlog_group(inst) if inst is not None else None

    def list_commands(self, ctx: click.Context) -> list[str]:
        names = set(super().list_commands(ctx))
        workspace = _ambient_workspace()
        if workspace is not None:
            names |= set(_mounted_groups(workspace))
        return sorted(names)


__all__ = [
    "DuplicateGroupNameError",
    "RackGroup",
    "build_backlog_group",
    "build_update_command",
    "group_name",
]
