"""The one batch-launch wiring behind ``ai-hats agent`` and ``execute --batch``.

Both commands already ran the same ``execute`` pipeline through the same
Automate runner, but each hand-built the funnel and repeated the post-run
report verbatim — and the copies drifted: ``execute`` grew ``--provider``,
``agent`` never did (HATS-1218). One wiring, so the next flag is added once.
"""

from __future__ import annotations

import json
import sys
from typing import NoReturn

from ai_hats_core.layout import ProjectLayout

import click

from ai_hats_observe.artifacts import METRICS_JSON
from ai_hats_wt import IsolationMode
from ..pipeline import run_pipeline
from ..session_policy import (
    Automate,
    MaterializedRole,
    SessionOutcome,
    SessionRecording,
    SessionRunParams,
)
from ..pipeline_catalog import EXECUTE
from ._helpers import console


def run_batch(
    layout: ProjectLayout,
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
    project_dir = layout.root
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import build_composition_payload, make_session_manager

    # The seam's typed errors (unknown role / unknown provider / no
    # provider) render at the root group — cli/_helpers.dispatch_friendly_error.
    result = run_pipeline(
        EXECUTE,
        SessionRunParams(
            layout=layout,
            role=MaterializedRole(
                name=role,
                composition=build_composition_payload(
                    project_dir,
                    role_override=role,
                    provider_name=provider,
                    interactive=False,
                ),
            ),
            # The CLI (integrator) injects the observe writer handles —
            # runners no longer construct them.
            recording=SessionRecording(
                manager=make_session_manager(layout),
                tracer_factory=SidecarTracer,
            ),
            annotations=tags,
            harness=Automate(
                prompt=task,
                model=model,
                isolation=IsolationMode(isolation),
                ticket_id=ticket,
            ),
        ),
    )

    _report(SessionOutcome.of(result), as_json=as_json)


def _report(result: SessionOutcome, *, as_json: bool) -> NoReturn:
    """Print the session summary (or its JSON) and exit with the agent's code."""
    session_id, session_dir = result.require_session()
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

    sys.exit(result.exit_code_or(int(metrics.get("exit_code", 1))))
