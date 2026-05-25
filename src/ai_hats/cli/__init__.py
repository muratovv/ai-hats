"""CLI interface — Click-based command-line tool.

`main_entry` is the console-script entry point exported via `pyproject.toml`.
It thin-wraps `main` (the click group) to make `--tree` order-independent
relative to `--help`. Subcommands are defined in sibling modules (assembly,
task, worktree, …) and mounted onto `main` at the bottom of this file.
"""

from __future__ import annotations

import sys

import click

from .. import __version__
from ._helpers import console


class _PassthroughGroup(click.Group):
    """Click group that treats unknown flag-like leftover args as extras
    instead of failing with 'No such command'. HATS-087.

    Click 8.x splits the parser leftover into ``ctx._protected_args[:1]``
    (the candidate subcommand name) and ``ctx.args[1:]``. If the first
    leftover token starts with ``-``, it is a flag the user wants
    forwarded to the underlying provider, NOT a subcommand. This override
    moves those tokens back into ``ctx.args`` so the no-subcommand path
    runs and the bare ``def main(ctx, ...)`` body sees them.

    No-op on click 9.x where ``_protected_args`` is removed and ``args``
    already contains all leftover tokens — the ``getattr`` defensiveness
    handles the absence gracefully.

    Caveat: subcommands whose name starts with ``-`` would be mis-routed.
    The project has none today; if one is added, this override needs
    updating.

    TODO(HATS-120b): drop once Click 9 is pinned.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        result = super().parse_args(ctx, args)
        protected = getattr(ctx, "_protected_args", None)
        if protected and protected[0].startswith("-"):
            ctx.args = list(protected) + list(ctx.args)
            ctx._protected_args = []
        return result


def _tree_callback(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    """Render the full command tree and exit. Eager — fires before group body."""
    if not value or ctx.resilient_parsing:
        return
    from ._tree import print_full_tree

    print_full_tree(ctx.find_root().command, console)
    ctx.exit()


@click.group(
    cls=_PassthroughGroup,
    invoke_without_command=True,
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
        "allow_interspersed_args": False,
    },
)
@click.version_option(version=__version__)
@click.option("--provider", "-p", default=None, help="Provider override (gemini/claude)")
@click.option("--role", "-r", default=None, help="Role override")
@click.option(
    "--tag",
    "tags_raw",
    multiple=True,
    help="Custom tag k=v for this session (repeatable, max 20). "
    "Stored in metrics.json under 'tags' for later query.",
)
@click.option(
    "--tree",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_tree_callback,
    help="Print the full command tree (man-style) and exit.",
)
@click.pass_context
def main(ctx, provider: str | None, role: str | None, tags_raw: tuple[str, ...]):
    """ai-hats — AI agent role composition framework.

    Without a subcommand, launches a wrapped provider CLI session.
    Unknown flags are passed through to the provider.
    """
    # HATS-213: heal a half-finished self-update (missing runtime dep) before
    # touching anything else. On success this re-execs the same command in a
    # fresh interpreter; on failure it sys.exit(1)s with a rescue command.
    from .._bootstrap import bootstrap_or_die

    bootstrap_or_die()

    if ctx.invoked_subcommand is None:
        from ..tags import TagValidationError, parse_tags

        try:
            tags = parse_tags(tags_raw)
        except TagValidationError as e:
            raise click.BadParameter(str(e), param_hint="--tag") from e
        _launch_session(
            provider=provider,
            role=role,
            extra_args=ctx.args,
            tags=tags or None,
        )


def _launch_session(
    provider: str | None = None,
    role: str | None = None,
    extra_args: list[str] | None = None,
    tags: dict[str, str] | None = None,
):
    """Launch a wrapped provider CLI session via the ``human`` pipeline."""
    from ..pipeline.harness import PipelineHarness
    from ..pipeline.steps.compose import RoleNotFoundError
    from ._helpers import _project_dir

    project_dir = _project_dir()
    # NB: role=None is intentional when --role is omitted. WrapRunner
    # internally resolves to cfg.active_role / cfg.default_role through the
    # permanent-assembly path. Resolving here would force the shadow-
    # override path with redundant compose work — keep behaviour identical
    # to pre-migration.

    try:
        with PipelineHarness("human", project_dir) as h:
            final = h.run(
                {
                    "role": role,
                    "interactive": True,
                    "project_dir": project_dir,
                    "provider": provider,
                    "extra_args": list(extra_args or []),
                    "tags": tags,
                }
            )
    except RoleNotFoundError as exc:
        # HATS-507: friendly error for `--role <bogus>`. The compose step
        # raises this typed exception with the sorted list of available
        # role names; render without a traceback and exit 2 (Click usage
        # error convention).
        click.echo(f"Error: Role {exc.role!r} not found.\n", err=True)
        click.echo("Available roles:", err=True)
        for name in exc.available:
            click.echo(f"  - {name}", err=True)
        click.echo(
            "\nHint: 'ai-hats list roles' shows the full table.", err=True,
        )
        sys.exit(2)
    sys.exit(int(final.get("exit_code", 1)))


# ----- Command registration -----
# Each submodule defines its command(s)/group(s) using plain @click decorators.
# We mount them here. Keeping registration centralized means `main --help`
# ordering is explicit and a single place to add/remove commands.

from . import (  # noqa: E402
    agent as agent_mod,
    assembly,
    attach as attach_mod,
    config as config_mod,
    execute as execute_mod,
    hyp as hyp_mod,
    list_cmd,
    maintenance,
    proposal as proposal_mod,
    reflect as reflect_mod,
    session,
    task,
    worktree,
)

# Config — set + customize + status nest under it (HATS-241, HATS-242).
# All four touch ai-hats.yaml composition; status is the readout.
config_mod.config.add_command(assembly.set_role)
config_mod.config.add_command(assembly.customize)
config_mod.config.add_command(assembly.status)
config_mod.config.add_command(assembly.show_prompt)  # HATS-452 Phase 1
main.add_command(config_mod.config)


# 'self' — framework lifecycle (HATS-241). Convention: rustup self update,
# gh extension self ... — instantly signals 'operations on the tool itself,
# not on your project'.
#
# HATS-407: ``self rollback`` removed. Per-session compose (HATS-294) plus
# yaml-only ``config set`` means the only mutable on-disk state is yaml +
# git-tracked scaffold files — ``git checkout`` is the user-facing recovery
# path.
@click.group("self")
def self_group():
    """Manage the ai-hats installation itself (init, clean, update)."""


self_group.add_command(assembly.init)
self_group.add_command(assembly.clean)
self_group.add_command(maintenance.update)
# HATS-415: ``self migrate-v07`` removed — migration is inline in ``bump``.
# HATS-470: ``self bump`` removed from CLI surface; the operation now runs
# via :mod:`ai_hats._bump_internal` (subprocess from ``self update``,
# preserves HATS-400 fresh-interpreter semantics) and inline from ``init``.
main.add_command(self_group)

# List
main.add_command(list_cmd.list_cmd)

# Execute — unified launch primitive (HATS-260). Wraps WrapRunner / SubAgentRunner.
main.add_command(execute_mod.execute_cmd)

# Agent — sub-agent launcher (HATS-242, was 'run'). Now a thin wrapper over execute.
main.add_command(agent_mod.run_subagent)

# Worktree
main.add_command(worktree.wt)

# Session (observability + retro generation)
main.add_command(session.session)

# Task management — hyp + proposal nest under it (HATS-241).
# All three are backlog artifacts, so they live as siblings:
#   ai-hats task list / create / ...
#   ai-hats task hyp ...
#   ai-hats task proposal ...
task.task.add_command(hyp_mod.hyp)
task.task.add_command(proposal_mod.proposal)
task.task.add_command(attach_mod.attach)
main.add_command(task.task)

# Reflect (post-session retro)
main.add_command(reflect_mod.reflect)


# Root-level options that consume the next argv token as their value.
# Used by `_extract_tree_path` to skip option values when collecting path
# tokens after `--tree`.
_ROOT_VALUE_OPTS = {"--provider", "-p", "--role", "-r", "--tag"}


def _extract_tree_path(argv: list[str]) -> list[str]:
    """Pull the subtree path out of `argv` — tokens **after** `--tree` only.

    Supported form: ``ai-hats --tree <group> [<sub> ...]``. Reverse order
    (``ai-hats <group> --tree``) is intentionally not supported — tokens
    before ``--tree`` are ignored. Empty result means «render full tree».
    """
    try:
        start = argv.index("--tree")
    except ValueError:
        return []
    path: list[str] = []
    j = start + 1
    while j < len(argv):
        a = argv[j]
        if a == "--help":
            j += 1
            continue
        if a in _ROOT_VALUE_OPTS:
            j += 2  # skip option and its value
            continue
        if any(a.startswith(opt + "=") for opt in _ROOT_VALUE_OPTS):
            j += 1
            continue
        if a.startswith("-"):
            j += 1  # bare flag (e.g. --json, --version)
            continue
        path.append(a)
        j += 1
    return path


def main_entry() -> None:
    """Console-script entry point.

    Intercepts ``--tree`` before click parses, so:
      - ``ai-hats --tree`` renders the full tree;
      - ``ai-hats --tree <group> [<sub>...]`` renders that subtree;
      - ``ai-hats --help --tree [<path>]`` works (``--help`` ignored when
        ``--tree`` is present, regardless of order).

    Without this shim, click's eager-flag ordering would short-circuit
    ``--help --tree`` to the default help, and click has no native way
    to attach an optional positional path to a top-level flag.

    HATS-337: the legacy ``_maybe_reexec_into_local_venv`` python wrapper
    was removed — the bash launcher (HATS-339) is now the single
    host-level entry-point and owns venv selection / re-exec.
    """
    if "--tree" in sys.argv[1:]:
        from ._tree import print_subtree

        path = _extract_tree_path(sys.argv[1:])
        print_subtree(main, path, console)
        sys.exit(0)
    main()
