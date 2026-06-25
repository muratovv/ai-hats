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
go through ``PipelineHarness`` over a built-in YAML pipeline (HATS-269).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ._helpers import _project_dir, console


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
    default="discard",
    type=click.Choice(["discard", "squash", "branch"]),
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
    from ..pipeline.harness import PipelineHarness
    from ..pipeline.steps.compose import RoleNotFoundError
    from ..tags import TagValidationError, parse_tags
    from ._helpers import _handle_role_not_found

    # HATS-827: empty role builds the git-invalid branch agent//<sid>; fail at
    # the boundary instead of crashing deep in worktree creation.
    if not interactive and not role:
        raise click.BadParameter(
            "--batch requires a role. To launch a sub-agent use "
            "'ai-hats agent <role> --task ...'; or pass -r/--role.",
            param_hint="--role",
        )

    try:
        tags = parse_tags(tags_raw)
    except TagValidationError as e:
        raise click.BadParameter(str(e), param_hint="--tag") from e

    project_dir = _project_dir()
    prompt_text = _resolve_prompt(prompt_arg, project_dir)

    try:
        with PipelineHarness("execute", project_dir) as h:
            # Interactive mode: provider CLI receives prompt as the first
            # positional arg in extra_args. The pipeline's resolve_prompt
            # step reads prompt_path → prompt_text and launch_provider then
            # prepends prompt_text to extra_args. We materialize the prompt
            # here so the harness contract (Path-only inputs) is preserved.
            final = h.run({
                "role": role,
                "interactive": interactive,
                "project_dir": project_dir,
                "prompt_path": h.materialize_prompt(prompt_text),
                "provider": provider,
                "model": model,
                "isolation": isolation,
                "ticket": ticket,
                "tags": tags or None,
                "extra_args": list(extra_args),
            })
    except RoleNotFoundError as exc:
        # HATS-547 / S-CLI-20: same friendly handler as ``_launch_session``;
        # pre-fix this exception bubbled up as a 9-frame traceback. Both
        # ``--interactive`` and ``--batch`` reach this catch because
        # ``compose_role`` runs before either runner branches.
        _handle_role_not_found(exc)

    if interactive:
        sys.exit(int(final.get("exit_code", 1)))

    # Batch mode: read metrics for --json output, print summary, exit.
    session_id = final["session_id"]
    session_dir = final["session_dir"]
    metrics_path = session_dir / "metrics.json"
    metrics: dict = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text())
        except (json.JSONDecodeError, OSError):
            metrics = {}

    if as_json:
        payload = {
            **metrics,
            "session_id": session_id,
            "session_dir": str(session_dir),
        }
        click.echo(json.dumps(payload, sort_keys=True))
    else:
        console.print(f"[green]Sub-agent completed[/]: {session_id}")
        console.print(f"  Session dir: {session_dir}")

    sys.exit(int(final.get("exit_code", metrics.get("exit_code", 1))))
