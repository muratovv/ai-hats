"""The one batch-launch wiring behind ``ai-hats agent`` and ``execute --batch``.

Both commands already ran the same ``execute`` pipeline through the same
Automate runner, but each hand-built the funnel and repeated the post-run
report verbatim — and the copies drifted: ``execute`` grew ``--provider``,
``agent`` never did (HATS-1218). One wiring, so the next flag is added once.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn

import click

from ai_hats_observe.artifacts import METRICS_JSON
from ai_hats_wt import IsolationMode
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


def run_batch(
    project_dir: Path,
    *,
    role: str,
    task: str | None = None,
    provider: str | None = None,
    model: str = "",
    isolation: str = IsolationMode.DISCARD.value,
    ticket: str = "",
    tags: dict[str, str] | None = None,
    as_json: bool = False,
) -> NoReturn:
    """Run one Automate session, report it, and exit with the sub-agent's code.

    No ``extra_args`` parameter by design: the ``provider`` step forwards it on
    the HITL branch only (``steps/launch.py``), so accepting one here would ship
    exactly the ignored knob HATS-1218 exists to remove.
    """
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import (
        MissingProviderError,
        RoleNotFoundError,
        build_composition_payload,
    )
    from ..composition_seam import make_session_manager
    from ..pipeline.harness import PipelineHarness
    from ..providers import UnknownProviderError
    from ._helpers import (
        _handle_missing_provider,
        _handle_role_not_found,
        _handle_unknown_provider,
    )

    try:
        with PipelineHarness(PIPELINE_EXECUTE, project_dir) as h:
            final = h.run({
                KEY_ROLE: role,
                KEY_INTERACTIVE: False,
                KEY_PROJECT_DIR: project_dir,
                KEY_PROMPT_PATH: h.materialize_prompt(task),
                KEY_MODEL: model,
                KEY_ISOLATION: isolation,
                KEY_TICKET: ticket,
                KEY_TAGS: tags or None,
                KEY_COMPOSITION: build_composition_payload(
                    project_dir,
                    role_override=role,
                    provider_name=provider,
                    interactive=False,
                ),
                # HATS-867: the CLI (integrator) injects the observe writer
                # handles — runners no longer construct them.
                KEY_SESSION_MGR: make_session_manager(project_dir),
                KEY_TRACER_FACTORY: SidecarTracer,
            })
    except RoleNotFoundError as exc:
        # HATS-545 / HATS-547: friendly stderr + exit 2, never a 9-frame
        # traceback. Shared with the bare-launch surface.
        _handle_role_not_found(exc)
    except UnknownProviderError as exc:
        # HATS-1218: the provider analogue (HATS-965), until now reachable only
        # from bare ``ai-hats`` because no batch surface honoured ``-p``.
        _handle_unknown_provider(exc)
    except MissingProviderError as exc:
        # HATS-1224: the absent-provider sibling — an emptied ``provider:``.
        _handle_missing_provider(exc)

    _report(final, as_json=as_json)


def _report(final: dict, *, as_json: bool) -> NoReturn:
    """Print the session summary (or its JSON) and exit with the agent's code."""
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
