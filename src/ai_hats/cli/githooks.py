"""The detached dispatcher's resolve entry point (HATS-1337, ADR-0020 D3).

Two faces of one function: ``ai-hats githooks resolve`` for a human asking which
gates a commit would run, and ``githooks_main`` for the dispatcher.
Machine-facing output is one ``<kind>\\t<path>`` line per record on
stdout; everything human goes to stderr.

Lives in the integrator layer because it composes a role — ``githooks_resolve``
itself stays a brick over a ready ``CompositionResult`` (HATS-865).
"""

from __future__ import annotations

from pathlib import Path

import click


def build_records(assembler, event: str) -> tuple[list[str], list[str]]:
    """``(stdout_lines, stderr_lines)`` for ``event`` — the dispatcher's payload.

    Shared by both entry points so the human-facing command and the module the
    dispatcher spawns cannot drift.
    """
    from ..githooks_resolve import resolve_git_gates
    from ..hooks_manager import GITHOOKS_BYPASS_JOURNAL
    from ..materialize import compose_for_role
    from ..paths import builtin_library_hooks

    cfg = assembler.project_config
    role = cfg.active_role or cfg.default_role
    if not role:
        return [], ["ai-hats: no role composed — no git gates to run"]

    resolution = resolve_git_gates(compose_for_role(assembler, role), event)
    out: list[str] = []
    err = [f"ai-hats: git gate refused — {r}" for r in resolution.refusals]

    hooks_root = builtin_library_hooks(assembler.project_dir)
    journal = None if hooks_root is None else hooks_root / GITHOOKS_BYPASS_JOURNAL
    if journal is not None and journal.is_file():
        out.append(f"journal\t{journal}")
    else:
        # Every hatch branch sources this; without it the gates still run but
        # stop recording bypasses (HATS-1407).
        err.append("ai-hats: bypass journal not found — gate bypasses will NOT be recorded")

    out.extend(f"gate\t{gate.path}" for gate in resolution.gates)
    return out, err


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
    """Print the gate scripts the composed role declares for EVENT."""
    from ._helpers import _assembler

    out, err = build_records(_assembler(project_dir), event)
    for line in err:
        click.echo(line, err=True)
    for line in out:
        click.echo(line)
