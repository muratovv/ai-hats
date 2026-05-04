"""`ai-hats reflect-session` — manual entry point for ReflectSessionRunner.

Runs reflect-session role on a single session, producing a hats-reflect-session/v1
retro plus side-effects (HYP validation_log appends + proposal create/vote via
sub-CLI). Auto-trigger from session-end uses --background to detach from caller.
"""

from __future__ import annotations

import sys

import click

from ..retro.reflect_session import ReflectSessionError, ReflectSessionRunner
from ._helpers import _project_dir, console


@click.command("reflect-session")
@click.option(
    "--session", "session_id", required=True,
    help="Session id (YYYYMMDD-HHMMSS-N) to reflect on",
)
@click.option(
    "--background", is_flag=True,
    help="Run as detached background process (used by auto-trigger).",
)
@click.option(
    "--max-retries", type=int, default=1, show_default=True,
)
def reflect_session(session_id: str, background: bool, max_retries: int):
    """Run reflect-session role on one session and validate output.

    On any failure a meta-proposal is filed programmatically — the command
    still exits non-zero so the caller can react, but the proposal serves
    as the durable audit record.
    """
    if background:
        _spawn_detached(session_id, max_retries)
        return

    project_dir = _project_dir()
    runner = ReflectSessionRunner(project_dir)
    try:
        path = runner.run(session_id, max_retries=max_retries)
    except ReflectSessionError as exc:
        console.print(
            f"[yellow]reflect-session failed for {session_id}:[/yellow] {exc}\n"
            "Meta-proposal filed in .agent/backlog/proposals/."
        )
        sys.exit(2)
    else:
        console.print(f"[green]✓[/green] reflect-session saved to {path}")


def _spawn_detached(session_id: str, max_retries: int) -> None:
    """Re-invoke ourselves in a new process group, return immediately."""
    import subprocess

    project_dir = _project_dir()
    log_path = (
        project_dir / ".gitlog" / f"session_{session_id}" / "retro.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m", "ai_hats.cli.reflect_session_main",
                session_id,
                str(max_retries),
            ],
            cwd=str(project_dir),
            stdout=f,
            stderr=f,
            start_new_session=True,
        )
    console.print(f"[dim]reflect-session spawned (pid={proc.pid}, bg)[/dim]")
