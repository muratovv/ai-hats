"""CLI interface — Click-based command-line tool.

`main` is the console-script entry point exported via `pyproject.toml`.
Subcommands are defined in sibling modules (assembly, task, worktree, …)
and mounted onto `main` at the bottom of this file.
"""

from __future__ import annotations

import sys

import click

from .. import __version__
from ._helpers import _project_dir, console


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
            provider=provider, role=role, extra_args=ctx.args,
            tags=tags or None,
        )


def _launch_session(
    provider: str | None = None,
    role: str | None = None,
    extra_args: list[str] | None = None,
    tags: dict[str, str] | None = None,
):
    """Launch a wrapped provider CLI session."""
    from ..models import ProjectConfig
    from ..runtime import WrapRunner

    project_dir = _project_dir()
    config = ProjectConfig.from_yaml(project_dir / "ai-hats.yaml")

    effective_provider = provider or config.provider
    if not effective_provider:
        console.print("[red]No provider configured[/]. Run: ai-hats set -p <provider>")
        sys.exit(1)

    runner = WrapRunner(project_dir)
    exit_code = runner.run(
        effective_provider,
        role_override=role,
        extra_args=extra_args or None,
        tags=tags,
    )
    sys.exit(exit_code)


# ----- Command registration -----
# Each submodule defines its command(s)/group(s) using plain @click decorators.
# We mount them here. Keeping registration centralized means `main --help`
# ordering is explicit and a single place to add/remove commands.

from . import (  # noqa: E402
    assembly,
    config as config_mod,
    hyp as hyp_mod,
    list_cmd,
    maintenance,
    proposal as proposal_mod,
    reflect as reflect_mod,
    run as run_mod,
    session,
    task,
    worktree,
)

# Assembly commands
main.add_command(assembly.init)
main.add_command(assembly.set_role)
main.add_command(assembly.customize)
main.add_command(assembly.status)
main.add_command(assembly.bump)
main.add_command(assembly.rollback)
main.add_command(assembly.clean)
main.add_command(assembly.whoami)
main.add_command(assembly.token_stats)

# Config
main.add_command(config_mod.config)

# List
main.add_command(list_cmd.list_cmd)

# Run (sub-agent launcher)
main.add_command(run_mod.run_subagent)

# Worktree
main.add_command(worktree.wt)

# Session (observability + retro generation)
main.add_command(session.session)

# Task management
main.add_command(task.task)

# Maintenance
main.add_command(maintenance.update)
main.add_command(maintenance.migrate)

# Hypothesis backlog (HATS-210)
main.add_command(hyp_mod.hyp)
main.add_command(proposal_mod.proposal)
main.add_command(reflect_mod.reflect)
