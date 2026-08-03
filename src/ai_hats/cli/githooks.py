"""``ai-hats githooks resolve`` — the detached dispatcher's resolve entry point.

The dispatcher (ADR-0020 D3) spawns this at commit time to learn which gates the
composed role declares for an event. Machine-facing: one ``<kind>\\t<path>`` line
per record on stdout, everything human on stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click


@click.group("githooks")
def githooks_group():
    """Inspect the git-hook gates the composed role installs."""


@githooks_group.command("resolve")
@click.argument("event")
@click.option(
    "--project-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Project to resolve against. The dispatcher passes the main checkout "
    "explicitly, because a commit's cwd may be a linked worktree.",
)
def resolve_cmd(event: str, project_dir: Path | None) -> None:
    """Print the gate scripts the composed role declares for EVENT.

    Output is one ``<kind>\\t<path>`` line per record: ``gate`` for each script
    to run, in chain order, and ``journal`` for the bypass-journal helper the
    gates source. A non-zero exit means the set could not be resolved — the
    dispatcher fails OPEN on that, so this never wedges a commit.
    """
    from ..githooks_resolve import resolve_git_gates
    from ..hooks_manager import GITHOOKS_BYPASS_JOURNAL
    from ..materialize import compose_for_role
    from ..paths import builtin_library_hooks
    from ._helpers import _assembler

    asm = _assembler(project_dir)
    cfg = asm.project_config
    role = cfg.active_role or cfg.default_role
    if not role:
        click.echo("ai-hats: no role composed — no git gates to run", err=True)
        return

    result = compose_for_role(asm, role)
    resolution = resolve_git_gates(result, event)

    for refusal in resolution.refusals:
        click.echo(f"ai-hats: git gate refused — {refusal}", err=True)

    hooks_root = builtin_library_hooks(asm.project_dir)
    journal = None if hooks_root is None else hooks_root / GITHOOKS_BYPASS_JOURNAL
    if journal is not None and journal.is_file():
        click.echo(f"journal\t{journal}")
    else:
        # Every hatch branch sources this; without it the gates still run but
        # stop recording bypasses (HATS-1407).
        click.echo(
            "ai-hats: bypass journal not found — gate bypasses will NOT be recorded",
            err=True,
        )

    for gate in resolution.gates:
        click.echo(f"gate\t{gate.path}")

    sys.stdout.flush()
