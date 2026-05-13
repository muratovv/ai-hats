"""`ai-hats self use-local` / `use-global` — opt-in local venv lifecycle.

HATS-318. The local venv lives at ``<ai_hats_dir>/.venv/``; activation is
implicit via the wrapper re-exec in :func:`ai_hats.cli.main_entry`. Default
install path (global pipx) is unchanged.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import click

from .. import paths
from ._helpers import _project_dir, console
from .maintenance import GIT_INSTALL_URL


@click.command("use-local")
@click.option(
    "--python",
    "python_bin",
    default=None,
    help="Python interpreter to base the venv on (default: current sys.executable).",
)
def use_local(python_bin: str | None) -> None:
    """Create opt-in local venv at <ai_hats_dir>/.venv/ and install ai-hats.

    Idempotent: if the venv already exists, prints a notice and exits 0.
    """
    project_dir = _project_dir()
    venv = paths.local_venv_path(project_dir)
    py = python_bin or sys.executable

    if venv.exists():
        console.print(f"[yellow]Local venv already exists[/]: {venv}")
        console.print("Use [bold]ai-hats self use-global[/] first to recreate.")
        return

    console.print(f"Creating venv at [bold]{venv}[/] (base: {py})")
    try:
        subprocess.run([py, "-m", "venv", str(venv)], check=True)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]venv creation failed[/]: {exc}")
        sys.exit(1)

    pip = venv / "bin" / "pip"
    console.print("Installing ai-hats into local venv...")
    try:
        subprocess.run(
            [str(pip), "install", "--no-cache-dir", f"ai-hats @ {GIT_INSTALL_URL}"],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]ai-hats install failed[/]: {exc}")
        # Roll back the half-built venv so the next try is clean.
        shutil.rmtree(venv, ignore_errors=True)
        sys.exit(1)

    console.print(
        f"[green]Local venv active.[/] Subsequent `ai-hats` calls re-exec "
        f"through {venv}/bin/python."
    )


@click.command("use-global")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def use_global(yes: bool) -> None:
    """Remove the opt-in local venv; revert to global pipx/pip ai-hats."""
    project_dir = _project_dir()
    venv = paths.local_venv_path(project_dir)

    if not venv.exists():
        console.print("[dim]No local venv present — already on global.[/]")
        return

    if not yes:
        click.confirm(f"Remove {venv}?", abort=True)

    shutil.rmtree(venv)
    console.print(f"[green]Removed[/] {venv}")
