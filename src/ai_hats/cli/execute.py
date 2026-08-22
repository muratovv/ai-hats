"""`ai-hats execute` — unified primitive for launching a provider session.

One command, two modes:

- ``--interactive`` (default) → ``WrapRunner`` (PTY-attached, replaces the
  shell). Same path as bare ``ai-hats``.
- ``--batch`` → ``SubAgentRunner`` (non-interactive subprocess with captured
  output). Same path as ``ai-hats agent``.

The ``--prompt`` flag resolves either to a file under
``initial_injections/<name>.md`` across the full ``library_paths`` chain
(by short name, last-wins — HATS-445) or to a filesystem path. The
resolved content becomes the first user-visible message.

All entry-points (bare ``ai-hats``, ``ai-hats agent``, ``ai-hats reflect *``)
go through ``pipeline.launch`` over a built-in YAML pipeline (HATS-269).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from click.core import ParameterSource

from ai_hats_wt import IsolationMode
from ..pipeline import Hitl, MaterializedRole, RunParams, SessionRecording, run_pipeline
from ..pipeline_catalog import EXECUTE
from ._helpers import _project_dir


def _resolve_prompt(arg: str | None, project_dir: Path) -> str | None:
    """Resolve ``--prompt`` into the text to inject as first user message.

    Lookup order (HATS-445):
      1. ``arg is None`` → return ``None``.
      2. ``initial_injections/<arg>.md`` across the full ``library_paths``
         chain via :meth:`LibraryResolver.resolve_injection` — last-wins,
         so a project-local override beats user-global beats built-in.
      3. ``arg`` as a filesystem path (absolute or cwd-relative).
      4. Fallback: treat ``arg`` as raw prompt text.

    A path-shaped arg (contains ``/`` or ends with ``.md`` / ``.txt``) that
    does not resolve to an existing file fails fast — likely a typo, not
    intentional raw text.
    """
    if arg is None:
        return None
    from ..assembler import Assembler

    inj_path = Assembler(project_dir).resolver.resolve_injection(arg)
    if inj_path is not None:
        return inj_path.read_text()
    fs_path = Path(arg)
    if fs_path.is_file():
        return fs_path.read_text()
    # Path-shaped strings must point to a real file — anything else is text.
    if "/" in arg or arg.endswith((".md", ".txt")):
        raise click.BadParameter(
            f"--prompt {arg!r}: looks like a path but no such file. "
            f"Tried initial_injections/{arg}.md across library_paths and "
            f"{fs_path.resolve()}.",
            param_hint="--prompt",
        )
    return arg


# Flags the HITL runner cannot act on: ``WrapRunner.run`` takes only
# ``(extra_args, tags, pty_tap_factory)``. Param name → the spelling to echo.
_BATCH_ONLY_FLAGS = (
    ("model", "--model"),
    ("isolation", "--isolation"),
    ("ticket", "--ticket"),
    ("as_json", "--json"),
)


def _reject_inert_flags(interactive: bool, extra_args: tuple[str, ...]) -> None:
    """Refuse a flag the chosen mode cannot act on (HATS-1218).

    The help text said "(batch only)" while the CLI accepted the flag and
    dropped it, and ``--batch`` swallowed ``extra_args`` with no note at all —
    the same accept-and-ignore shape as the provider override. Follows the
    HATS-827 precedent below: fail at the boundary, naming the mode.
    """
    if not interactive:
        if extra_args:
            raise click.BadParameter(
                f"extra args {list(extra_args)} are interactive-only — the "
                "sub-agent runner takes none, so --batch would drop them. "
                "Pass the prompt via --prompt, or use --interactive.",
                param_hint="extra arguments",
            )
        return
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return
    named = [
        flag
        for name, flag in _BATCH_ONLY_FLAGS
        if ctx.get_parameter_source(name) == ParameterSource.COMMANDLINE
    ]
    if named:
        raise click.BadParameter(
            f"{', '.join(named)} {'is' if len(named) == 1 else 'are'} batch-only "
            "— the interactive runner cannot act on "
            f"{'it' if len(named) == 1 else 'them'}. Drop "
            f"{'it' if len(named) == 1 else 'them'}, or pass --batch.",
            param_hint=", ".join(named),
        )


@click.command(
    "execute",
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
    },
)
@click.option("--role", "-r", default=None, help="Role to compose")
@click.option("--provider", "-p", default=None, help="Provider override")
@click.option(
    "--interactive/--batch",
    default=True,
    help="Interactive PTY (default) or batch sub-agent.",
)
@click.option(
    "--prompt",
    "prompt_arg",
    default=None,
    help="Initial prompt: short name (resolves to "
    "initial_injections/<name>.md across library_paths, last-wins) "
    "or filesystem path.",
)
@click.option("--model", default="", help="Model override (batch only).")
@click.option(
    "--isolation",
    default=IsolationMode.DISCARD.value,
    type=click.Choice(
        [IsolationMode.DISCARD.value, IsolationMode.SQUASH.value, IsolationMode.BRANCH.value]
    ),
    help="Worktree isolation (batch only).",
)
@click.option("--ticket", default="", help="Ticket id for context (batch only).")
@click.option(
    "--tag",
    "tags_raw",
    multiple=True,
    help="Custom tag k=v (repeatable, max 20).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a single JSON object on completion (batch only).",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def execute_cmd(
    role: str | None,
    provider: str | None,
    interactive: bool,
    prompt_arg: str | None,
    model: str,
    isolation: str,
    ticket: str,
    tags_raw: tuple[str, ...],
    as_json: bool,
    extra_args: tuple[str, ...],
):
    """Launch a provider session with a composed role + optional initial prompt."""
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import build_composition_payload, make_session_manager
    from ..tags import TagValidationError, parse_tags
    from ._batch_launch import run_batch

    # HATS-827: empty role builds the git-invalid branch agent//<sid>; fail at
    # the boundary instead of crashing deep in worktree creation.
    if not interactive and not role:
        raise click.BadParameter(
            "--batch requires a role. To launch a sub-agent use "
            "'ai-hats agent <role> --task ...'; or pass -r/--role.",
            param_hint="--role",
        )
    _reject_inert_flags(interactive, extra_args)

    try:
        tags = parse_tags(tags_raw)
    except TagValidationError as e:
        raise click.BadParameter(str(e), param_hint="--tag") from e

    project_dir = _project_dir()
    prompt_text = _resolve_prompt(prompt_arg, project_dir)

    if not interactive:
        # HATS-1218: one Automate wiring, shared with ``ai-hats agent``.
        run_batch(
            project_dir,
            role=role,
            task=prompt_text,
            provider=provider,
            model=model,
            isolation=isolation,
            ticket=ticket,
            tags=tags,
            as_json=as_json,
        )

    # HATS-1228: the seam's typed errors render at the root group —
    # cli/_helpers.dispatch_friendly_error.
    result = run_pipeline(
        EXECUTE,
        RunParams(
            project_dir=project_dir,
            role=MaterializedRole(
                name=role,
                composition=build_composition_payload(
                    project_dir,
                    role_override=role,
                    provider_name=provider,
                    interactive=True,
                ),
            ),
            # HATS-867: the CLI (integrator) injects the observe writer handles —
            # runners no longer construct them.
            recording=SessionRecording(
                manager=make_session_manager(project_dir),
                tracer_factory=SidecarTracer,
            ),
            annotations=tags,
            # model / isolation / ticket are batch-only and already refused here
            # by _reject_inert_flags, so the HITL branch cannot carry them.
            harness=Hitl(prompt=prompt_text, extra_args=tuple(extra_args)),
        ),
    )

    sys.exit(result.exit_code_or(1))
