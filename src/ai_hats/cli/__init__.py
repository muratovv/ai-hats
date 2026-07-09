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

import click

from .. import __version__
from ..paths import ENV_AI_HATS_VENV
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
    from ..composition_seam import RoleNotFoundError, build_composition_payload
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import make_session_manager
    from ..pipeline.harness import PipelineHarness
    from ..pipeline.keys import (
        KEY_COMPOSITION,
        KEY_EXIT_CODE,
        KEY_EXTRA_ARGS,
        KEY_INTERACTIVE,
        KEY_PROJECT_DIR,
        KEY_PROVIDER,
        KEY_ROLE,
        KEY_SESSION_MGR,
        KEY_TAGS,
        KEY_TRACER_FACTORY,
        PIPELINE_HUMAN,
    )
    from ..providers import UnknownProviderError
    from ._helpers import (
        _handle_role_not_found,
        _handle_unknown_provider,
        _project_dir,
    )

    project_dir = _project_dir()

    try:
        with PipelineHarness(PIPELINE_HUMAN, project_dir) as h:
            # HATS-865: compose ONCE here (effective-role resolution + the
            # first-run set_role side effect live in the seam) and seed the
            # payload into the funnel; the launch step hands it to WrapRunner.
            final = h.run(
                {
                    KEY_ROLE: role,
                    KEY_INTERACTIVE: True,
                    KEY_PROJECT_DIR: project_dir,
                    KEY_PROVIDER: provider,
                    KEY_EXTRA_ARGS: list(extra_args or []),
                    KEY_TAGS: tags,
                    KEY_COMPOSITION: build_composition_payload(
                        project_dir,
                        role_override=role,
                        provider_name=provider,
                        interactive=True,
                    ),
                    # HATS-867: the CLI (integrator) injects the observe writer
                    # handles — runners no longer construct them.
                    KEY_SESSION_MGR: make_session_manager(project_dir),
                    KEY_TRACER_FACTORY: SidecarTracer,
                }
            )
    except RoleNotFoundError as exc:
        # HATS-507 contract, HATS-547 shared helper. Friendly stderr +
        # exit 2; no traceback. See ``_handle_role_not_found`` for the
        # full output shape.
        _handle_role_not_found(exc)
    except UnknownProviderError as exc:
        # HATS-965: friendly stderr + exit 2 for an unavailable ``-p`` provider,
        # mirroring the RoleNotFoundError arm. See ``_handle_unknown_provider``.
        _handle_unknown_provider(exc)
    sys.exit(int(final.get(KEY_EXIT_CODE, 1)))


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
    worktree,
)
from ai_hats_tracker.cli import (  # noqa: E402
    attach as attach_mod,
    hyp as hyp_mod,
    proposal as proposal_mod,
    task,
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

# HATS-934: tracker CLI (task/attach) defaults to wt-free constructors; the
# integrator wires the wt-coupled `_helpers` versions here so `ai-hats task`
# keeps its worktree UX (override the shared `_seam` — reaches every importer).
from ai_hats_tracker.cli import _seam  # noqa: E402
from ..paths import hypotheses_dir, proposals_dir, worktrees_dir  # noqa: E402
from ._helpers import (  # noqa: E402
    _guard_not_inside_linked_worktree,
    _project_dir,
    _task_manager,
)

_seam._MANAGER_FACTORY = _task_manager
_seam._PROJECT_DIR = _project_dir
_seam._GUARD_LINKED_WT = _guard_not_inside_linked_worktree
_seam._CONSOLE = console
_seam._WORKTREES_DIR = worktrees_dir
# hyp/prop path resolvers (HATS-935) — AI_HATS_DIR/yaml-aware integrator versions.
_seam._HYPOTHESES_DIR = hypotheses_dir
_seam._PROPOSALS_DIR = proposals_dir

# HATS-952: observe session-browse CLI (list/show/audit) defaults to wt-free
# resolvers; inject the integrator's AI_HATS_DIR/yaml-aware layout so
# `ai-hats session` keeps its exact paths + tag semantics.
from ai_hats_observe.cli import _seam as _observe_seam  # noqa: E402
from ..paths import runs_dir  # noqa: E402
from ..tags import parse_tag_filters  # noqa: E402

_observe_seam._PROJECT_DIR = _project_dir
_observe_seam._RUNS_DIR = runs_dir
_observe_seam._TAG_FILTER_PARSER = parse_tag_filters
_observe_seam._CONSOLE = console

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
        from pathlib import Path

        from ..paths import venv_path
        from ._helpers import _project_dir

        # The launcher pins the resolved venv via AI_HATS_VENV (HATS-647
        # pin-at-spawn); honour it verbatim so launcher and guard agree. Else
        # resolve from the project (venv_path already reads AI_HATS_VENV first).
        pinned = os.environ.get(ENV_AI_HATS_VENV)
        resolved_path = Path(pinned) if pinned else venv_path(_project_dir())
        # HATS-791 refinement: only a managed venv that ACTUALLY EXISTS can be
        # "shadowed". If the resolved venv is absent (no managed install for this
        # project), there is nothing to shadow — fail open (treat as unknown).
        # This keeps the guard to its true scope (a real managed venv exists but
        # we are running from a DIFFERENT one) and clears false-positives for
        # standalone / by-name installs in projects with no managed venv.
        resolved_venv = str(resolved_path) if resolved_path.exists() else None
    except Exception:  # noqa: BLE001 — fail open on ANY resolution error
        resolved_venv = None
    try:
        from .maintenance import _is_editable_install

        is_editable, _ = _is_editable_install()
    except Exception:  # noqa: BLE001 — fail open
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
    _guard_self_location()
    if "--tree" in sys.argv[1:]:
        from ._tree import print_subtree

        path = _extract_tree_path(sys.argv[1:])
        print_subtree(main, path, console)
        sys.exit(0)
    # HATS-839: a write op resolved to a non-project root — render the library
    # NotAnAiHatsProjectError as a friendly message instead of a traceback.
    from ..paths import NotAnAiHatsProjectError

    try:
        main()
    except NotAnAiHatsProjectError as exc:
        console.print(f"[red]Error:[/] {exc}")
        sys.exit(2)
