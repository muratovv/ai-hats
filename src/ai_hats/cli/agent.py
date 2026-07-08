"""`ai-hats agent` — launch a sub-agent session with an isolated worktree."""

from __future__ import annotations

import json
import sys

import click

from ai_hats_wt import IsolationMode
from ..paths import METRICS_JSON
from ..pipeline.keys import (
    KEY_COMPOSITION,
    KEY_EXIT_CODE,
    KEY_INTERACTIVE,
    KEY_ISOLATION,
    KEY_MODEL,
    KEY_PROMPT_PATH,
    KEY_PROJECT_DIR,
    KEY_ROLE,
    KEY_SESSION_DIR,
    KEY_SESSION_ID,
    KEY_SESSION_MGR,
    KEY_TAGS,
    KEY_TICKET,
    KEY_TRACER_FACTORY,
    PIPELINE_EXECUTE,
)
from ._helpers import console


@click.command("agent")
@click.argument("role")
@click.option("--ticket", default=None, help="Ticket/task ID for context")
@click.option("--model", default=None, help="Model override")
@click.option("--task", default=None, help="Task description")
@click.option(
    "--isolation",
    default=IsolationMode.DISCARD.value,
    type=click.Choice([IsolationMode.DISCARD.value, IsolationMode.SQUASH.value, IsolationMode.BRANCH.value]),
    help="Worktree isolation mode (default: discard)",
)
@click.option(
    "--tag",
    "tags_raw",
    multiple=True,
    help="Custom tag k=v (repeatable, max 20). Stored in metrics.json under 'tags' "
         "for later query via 'ai-hats session list --tag k=v'.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a single JSON object to stdout on completion (session_id, "
         "session_dir, exit_code, role, duration_s, tags, ...). Suppresses "
         "the human-readable summary. Pair with stable exit code propagation "
         "so orchestrators can fan out via parallel/xargs/CI scripts.",
)
def run_subagent(
    role: str,
    ticket: str | None,
    model: str | None,
    task: str | None,
    isolation: str,
    tags_raw: tuple[str, ...],
    as_json: bool,
):
    """Run a sub-agent with the given role.

    Exit codes (propagated from the sub-agent subprocess / runtime):

    - 0   — success
    - 1   — agent/runtime error
    - 2   — CLI usage error (invalid flags)
    - 124 — timeout (sub-agent exceeded wall-clock limit)
    - other non-zero — forwarded verbatim from provider CLI
    """
    from ..composition_seam import RoleNotFoundError, build_composition_payload
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import make_session_manager
    from ..pipeline.harness import PipelineHarness
    from ..tags import TagValidationError, parse_tags
    from ._helpers import _handle_role_not_found, _project_dir

    try:
        tags = parse_tags(tags_raw)
    except TagValidationError as e:
        raise click.BadParameter(str(e), param_hint="--tag") from e

    project_dir = _project_dir()
    try:
        with PipelineHarness(PIPELINE_EXECUTE, project_dir) as h:
            final = h.run({
                KEY_ROLE: role,
                KEY_INTERACTIVE: False,
                KEY_PROJECT_DIR: project_dir,
                KEY_PROMPT_PATH: h.materialize_prompt(task),
                KEY_MODEL: model or "",
                KEY_ISOLATION: isolation,
                KEY_TICKET: ticket or "",
                KEY_TAGS: tags or None,
                KEY_COMPOSITION: build_composition_payload(
                    project_dir, role_override=role,
                ),
                # HATS-867: the CLI (integrator) injects the observe writer
                # handles — runners no longer construct them.
                KEY_SESSION_MGR: make_session_manager(project_dir),
                KEY_TRACER_FACTORY: SidecarTracer,
            })
    except RoleNotFoundError as exc:
        # HATS-545 / S-CLI-05: same friendly handler as ``_launch_session``
        # and ``execute_cmd``; pre-fix this exception bubbled up as a
        # 9-frame traceback. Third "compose-then-run" entry-point to
        # share the helper (HATS-547 set the precedent).
        _handle_role_not_found(exc)

    session_id = final[KEY_SESSION_ID]
    session_dir = final[KEY_SESSION_DIR]
    metrics_path = session_dir / METRICS_JSON
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

    sys.exit(int(final.get(KEY_EXIT_CODE, metrics.get("exit_code", 1))))
