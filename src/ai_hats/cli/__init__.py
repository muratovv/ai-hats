"""CLI interface — Click-based command-line tool.

`main_entry` is the package entry point invoked by ``python -m ai_hats``
(``src/ai_hats/__main__.py``). HATS-790 (Alt 5) removed the
``[project.scripts] ai-hats`` console script, so ``python -m ai_hats`` is now
the only entry — no venv materialises a shadowable ``bin/ai-hats`` proxy.
``main_entry`` thin-wraps `main` (the click group) to make `--tree`
order-independent relative to `--help`. Subcommands are defined in sibling
modules (assembly, task, worktree, …) and mounted onto `main` at the bottom of
this file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from .. import __version__
from ._helpers import console


class _PassthroughGroup(click.Group):
    """Click group that treats unknown flag-like leftover args as extras
    instead of failing with 'No such command'. HATS-087 / HATS-1202.

    Click 8.x splits the parser leftover into ``ctx._protected_args[:1]``
    (the candidate subcommand name) and ``ctx.args[1:]``. If the first
    leftover token starts with ``-`` or is not a registered subcommand
    (HATS-1202), it is treated as provider flags or a bare positional prompt,
    NOT a subcommand. This override moves those tokens back into ``ctx.args``
    so the no-subcommand path runs and the bare ``def main(ctx, ...)`` body
    sees them.

    No-op on click 9.x where ``_protected_args`` is removed and ``args``
    already contains all leftover tokens — the ``getattr`` defensiveness
    handles the absence gracefully.

    Caveat: subcommands whose name starts with ``-`` or match a positional prompt
    would be mis-routed; registered subcommands always take precedence.

    TODO(HATS-120b): drop once Click 9 is pinned.
    """

    def invoke(self, ctx: click.Context):
        """Render the CLI's typed errors friendly, wherever they were raised.

        HATS-1228: every command reachable from this group — the bare-launch
        callback, subcommands, nested groups — funnels through here, so friendly
        handling is no longer per-site opt-in (``cli/reflect.py`` composed five
        times and caught nothing). Unregistered exceptions keep their traceback:
        this is a renderer, not a catch-all.
        """
        from ._helpers import dispatch_friendly_error

        try:
            return super().invoke(ctx)
        except Exception as exc:
            dispatch_friendly_error(exc)
            raise

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        result = super().parse_args(ctx, args)
        protected = getattr(ctx, "_protected_args", None)
        if protected and (
            protected[0].startswith("-") or self.get_command(ctx, protected[0]) is None
        ):
            ctx.args = list(protected) + list(ctx.args)
            ctx._protected_args = []
        return result

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        super().format_help(ctx, formatter)

        # HATS-1112: append provider-specific hints if a provider or role is resolvable.
        # Since --help is eager, ctx.params might not have parsed trailing flags, so we
        # fallback to a naive sys.argv scan to provide contextual help even for `ai-hats --help -p agy`.
        import sys
        from rich.console import Console
        from rich.table import Table
        from ..composition_seam import resolve_provider_for_help

        provider_name = ctx.params.get("provider")
        role_name = ctx.params.get("role")

        if not provider_name and not role_name:
            for i, arg in enumerate(sys.argv):
                if arg in ("-p", "--provider") and i + 1 < len(sys.argv):
                    provider_name = sys.argv[i + 1]
                elif arg in ("-r", "--role") and i + 1 < len(sys.argv):
                    role_name = sys.argv[i + 1]

        if not provider_name and not role_name:
            return

        provider = resolve_provider_for_help(provider_name, role_name)
        if not provider:
            return

        hints = provider.surface_hints()
        if not hints:
            return
        table = Table(
            title=f"Provider Hints ({provider.name})", show_header=True, header_style="bold magenta"
        )
        table.add_column("Name", style="cyan")
        table.add_column("Values", style="green")
        table.add_column("Comment", style="yellow")

        for hint in hints:
            table.add_row(hint.name, hint.values, hint.description)

        # Capture rich output to string
        import io

        buf = io.StringIO()
        capture_console = Console(file=buf, force_terminal=True, color_system="256")
        capture_console.print(table)

        formatter.write("\n")
        formatter.write(buf.getvalue())


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
@click.option(
    "--provider", "-p", default=None, help="Provider override (see `ai-hats list providers`)"
)
@click.option("--role", "-r", default=None, help="Role override")
@click.option(
    "--tag",
    "tags_raw",
    multiple=True,
    help="Custom tag k=v for this session (repeatable, max 20). "
    "Stored in metrics.json under 'tags' for later query.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Report what the launch would deliver (prompt, args, materialized "
    "files) and exit without spawning the provider. Writes nothing.",
)
@click.option(
    "--dry-run-json",
    "dry_run_json",
    is_flag=True,
    help="Machine-readable --dry-run payload.",
)
@click.option(
    "--dry-run-full",
    "dry_run_full",
    is_flag=True,
    help="With --dry-run: dump the composed prompt body, not just its path.",
)
@click.option(
    "--materialize",
    "materialize",
    is_flag=True,
    help="With --dry-run: write the materialized session tree to disk without spawning.",
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
def main(
    ctx,
    provider: str | None,
    role: str | None,
    tags_raw: tuple[str, ...],
    dry_run: bool,
    dry_run_json: bool,
    dry_run_full: bool,
    materialize: bool,
):
    """ai-hats — AI agent role composition framework.

    Without a subcommand, launches a wrapped provider CLI session.
    Positional text or unknown flags are passed through to the provider.
    """
    if ctx.invoked_subcommand is None:
        from ..tags import TagValidationError, parse_tags

        if "+" in ctx.args:
            raise click.UsageError(
                "bare '+' in provider arguments — a role spec with spaces must be quoted:\n"
                '       ai-hats -r "maintainer + leader"\n'
                "       or written without spaces:  ai-hats -r maintainer+leader"
            )

        if dry_run or dry_run_json or dry_run_full or materialize:
            _dry_run_session(
                provider=provider,
                role=role,
                extra_args=ctx.args,
                as_json=dry_run_json,
                full=dry_run_full,
                materialize=materialize,
            )
            return

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


def _dry_run_session(
    *,
    provider: str | None,
    role: str | None,
    extra_args: list[str] | None,
    as_json: bool,
    full: bool,
    materialize: bool = False,
) -> None:
    """Print what a launch would deliver; spawn nothing, write nothing unless materialize is set."""
    import json as _json

    from ..dry_run import dry_run_hitl
    from ._helpers import _project_dir

    # HATS-1228: the seam's typed errors render at the root group —
    # cli/_helpers.dispatch_friendly_error.
    report = dry_run_hitl(
        _project_dir(),
        role=role,
        provider=provider,
        extra_args=list(extra_args or []),
        materialize=materialize,
    )

    if as_json:
        click.echo(_json.dumps(report.to_dict(), indent=2))
    else:
        click.echo(report.render(full=full), nl=False)


def _launch_session(
    provider: str | None = None,
    role: str | None = None,
    extra_args: list[str] | None = None,
    tags: dict[str, str] | None = None,
):
    """Launch a wrapped provider CLI session via the ``human`` pipeline."""
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import build_composition_payload, make_session_manager
    from ..pipeline import run_pipeline
    from ..pipeline_catalog import HUMAN
    from ..session_policy import (
        Hitl,
        MaterializedRole,
        SessionOutcome,
        SessionRecording,
        SessionRunParams,
    )
    from ._helpers import _project_dir

    project_dir = _project_dir()

    # HATS-1228: the seam's typed errors render at the root group —
    # cli/_helpers.dispatch_friendly_error.
    result = run_pipeline(
        HUMAN,
        SessionRunParams(
            project_dir=project_dir,
            # HATS-865: compose ONCE here (effective-role resolution + the
            # first-run set_role side effect live in the seam) and seed the
            # payload into the funnel; the launch step hands it to WrapRunner.
            role=MaterializedRole(
                name=role,
                composition=build_composition_payload(
                    project_dir,
                    role_override=role,
                    provider_name=provider,
                    interactive=True,
                ),
            ),
            # HATS-867: the CLI (integrator) injects the observe writer
            # handles — runners no longer construct them.
            recording=SessionRecording(
                manager=make_session_manager(project_dir),
                tracer_factory=SidecarTracer,
            ),
            annotations=tags,
            harness=Hitl(extra_args=tuple(extra_args or ())),
        ),
    )
    sys.exit(SessionOutcome.of(result).exit_code_or(1))


# ----- Command registration -----
# Each submodule defines its command(s)/group(s) using plain @click decorators.
# We mount them here. Keeping registration centralized means `main --help`
# ordering is explicit and a single place to add/remove commands.

from . import (  # noqa: E402
    agent as agent_mod,
    assembly,
    config as config_mod,
    execute as execute_mod,
    list_cmd,
    maintenance,
    reflect as reflect_mod,
    session,
    wait as wait_mod,
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
    """Manage the ai-hats installation itself (init, update)."""


self_group.add_command(assembly.init)
# HATS-833: ``self sync-hooks`` removed — drift healing consolidated to session start.
self_group.add_command(maintenance.update)
# HATS-966: hidden self-heal for stale surface-plugin editables; the launcher
# calls it before exec when its probe flags a broken plugin (also runs in `update`).
self_group.add_command(maintenance.heal_editables)
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

# Wait — block-in-session until an event happens (HATS-986)
main.add_command(wait_mod.wait_cmd)

# HATS-1260: the legacy `ai-hats task` groups (task/hyp/proposal/attach) are
# unmounted — rack is the only backlog surface; the tracker package dies at HATS-1262.

# HATS-952: observe session-browse CLI (list/show/audit) defaults to wt-free
# resolvers; inject the integrator's AI_HATS_DIR/yaml-aware layout so
# `ai-hats session` keeps its exact paths + tag semantics.
from ai_hats_core.layout import ProjectLayout  # noqa: E402
from ai_hats_observe.cli import _seam as _observe_seam  # noqa: E402
from ..paths import ai_hats_dir  # noqa: E402
from ..tags import parse_tag_filters  # noqa: E402
from ._helpers import _project_dir  # noqa: E402


def _integrator_layout() -> ProjectLayout:
    # TODO(HATS-1606): step 6 replaces this with resolve_project().layout
    root = _project_dir()
    return ProjectLayout(root=root, base=ai_hats_dir(root))


_observe_seam._LAYOUT = _integrator_layout
_observe_seam._TAG_FILTER_PARSER = parse_tag_filters
_observe_seam._CONSOLE = console


def _observe_provider_adapter(provider: str):
    """Transcript discovery + parser of the surface that recorded the session.

    Backfill must read each session through its own provider, not a fixed one
    (HATS-1374). An unrecorded or retired provider name yields no reader, so the
    session is reported as having no transcript instead of being mis-parsed.
    """
    from ..surface_registry import UnknownSurfaceError, get_surface

    try:
        p = get_surface(provider)
    except (UnknownSurfaceError, ValueError):
        return None, None
    return p.resolve_transcript, p.transcript_parser()


_observe_seam._PROVIDER_ADAPTER = _observe_provider_adapter

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


# Pure-informational invocations that touch NO project state and so are safe to
# run from anywhere — never worth refusing (a shadow printing its version harms
# nothing). Skipping them also keeps the guard off the in-process ``main_entry``
# tree tests (which run with ``src`` on ``PYTHONPATH``, where editable detection
# can't see ``direct_url.json``). HATS-791.
_GUARD_EXEMPT_FLAGS = frozenset({"--version", "--help", "-h", "--tree"})


def _is_guard_exempt_invocation(argv: list[str]) -> bool:
    """True for pure-info invocations (``--version`` / ``--help`` / ``--tree``)
    or a bare ``ai-hats`` with no args (top-level help) — none resolve a project,
    so the self-location guard has nothing to protect."""
    if not argv:
        return True  # bare invocation → click prints help, no project work
    return any(a in _GUARD_EXEMPT_FLAGS for a in argv)


def _resolve_guard_target(project_dir: Path) -> str | None:
    """The venv this project resolves to, or ``None`` when there is none.

    ``venv_path`` IS the precedence chain, ``AI_HATS_VENV`` first and pair-scoped
    (ADR-0025 D3) — reading the pin separately honoured one that the launcher and
    ``venv_path`` both drop, refusing this project's own venv (HATS-1621).

    Only a venv that ACTUALLY EXISTS can be shadowed (HATS-791): an absent one
    means no managed install for this project, so there is nothing to shadow and
    the caller fails open on ``None``.
    """
    from ..paths import venv_path

    resolved = venv_path(project_dir)
    return str(resolved) if resolved.exists() else None


def _guard_self_location() -> None:
    """Refuse-and-instruct when running from a FOREIGN (non-managed) venv.

    HATS-791 backstop for the "shadow" problem (a stale ai-hats in some
    project app-venv reached ahead of the host launcher). Wired into
    :func:`main_entry` — the real-invocation path (launcher → ``python -m
    ai_hats`` → ``__main__`` → ``main_entry``) — and DELIBERATELY NOT into the
    bare ``main`` click group, so in-process ``CliRunner`` tests (which invoke
    ``main`` directly) never reach it and the guard cannot break the suite.

    Bias HARD toward fail-open: the shadow generator is already gone (HATS-790),
    so a missed shadow merely reproduces old behaviour while a false-positive
    bricks the CLI. Any resolution error → sanctioned (we never raise out of
    here). The actual sanctioned/foreign decision is the pure
    :func:`ai_hats.self_location.classify_invocation`.
    """
    from ..self_location import (
        SKIP_ENV_VAR,
        classify_invocation,
        remediation_text,
    )

    # Pure-info commands resolve no project state → nothing to protect.
    if _is_guard_exempt_invocation(sys.argv[1:]):
        return

    skip = os.environ.get(SKIP_ENV_VAR) == "1"
    running_prefix = sys.prefix

    # Resolve the venv ai-hats would pick for this project, and whether this is
    # an editable host clone — both wrapped so ANY failure fails open.
    resolved_venv: str | None = None
    is_editable = False
    try:
        from ._helpers import _project_dir

        resolved_venv = _resolve_guard_target(_project_dir())
    except Exception:  # silent-ok: fail open on ANY resolution error, per docstring
        resolved_venv = None
    try:
        from .maintenance import _is_editable_install

        is_editable, _ = _is_editable_install()
    except Exception:  # silent-ok: fail open on ANY resolution error, per docstring
        is_editable = False

    verdict = classify_invocation(
        running_prefix,
        resolved_venv,
        is_editable_install=is_editable,
        skip=skip,
    )
    if verdict == "foreign":
        print(remediation_text(running_prefix), file=sys.stderr)
        sys.exit(3)


def main_entry() -> None:
    """Package entry point — invoked by ``python -m ai_hats`` (HATS-790).

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

    HATS-791: self-location guard fires FIRST. Real invocations land here
    (launcher → ``python -m ai_hats`` → ``__main__`` → ``main_entry``); the
    in-process ``CliRunner`` calls ``main`` directly and so bypasses the guard.
    """
    try:
        _guard_self_location()
        if "--tree" in sys.argv[1:]:
            from ._tree import print_subtree

            path = _extract_tree_path(sys.argv[1:])
            print_subtree(main, path, console)
            sys.exit(0)
        # HATS-1228: same registry the root group dispatches through, kept here
        # too because this boundary also covers the pre-click phase above
        # (HATS-839: a write op resolved to a non-project root).
        from ._helpers import dispatch_friendly_error

        try:
            main()
        except Exception as exc:
            dispatch_friendly_error(exc)
            raise
    except Exception as exc:
        from ..self_heal import is_broken_install_exception

        if is_broken_install_exception(exc):
            try:
                from ._helpers import _handle_broken_install_or_die

                _handle_broken_install_or_die(exc)
            except Exception as fallback_exc:
                if fallback_exc is exc:
                    raise
                from ..startup_notices import show_fatal_notice_and_exit

                from ._helpers import broken_install_notice

                show_fatal_notice_and_exit(broken_install_notice(exc))
        raise
