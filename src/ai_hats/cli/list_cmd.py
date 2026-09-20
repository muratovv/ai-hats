"""`ai-hats list` — inspect roles/providers/traits/rules/skills in the library."""

from __future__ import annotations

import logging
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

import click

from ._entry import resolve_project
from ._helpers import _assembler, console

logger = logging.getLogger(__name__)


@click.group("list")
def list_cmd():
    """List available components."""
    pass


@list_cmd.command("roles")
def list_roles():
    """List available roles."""
    from rich.table import Table

    from ..models import ComponentType

    asm = _assembler(resolve_project().layout.root, prefer_cwd=True)
    names = asm.resolver.list_components(ComponentType.ROLE)
    if not names:
        console.print("[dim]No roles found[/]")
        return

    table = Table(show_header=True, header_style="bold", padding=(0, 1))
    table.add_column("Role", style="cyan", no_wrap=True)
    table.add_column("Traits", justify="right")
    table.add_column("Rules", justify="right")
    table.add_column("Skills", justify="right")
    table.add_column("Priorities", style="dim")

    for name in names:
        try:
            cfg = asm.resolver.resolve_config(name, ComponentType.ROLE)
        except Exception as exc:  # noqa: BLE001
            logger.warning("role %r: failed to load config: %s", name, exc)
            cfg = None
        if cfg:
            table.add_row(
                name,
                str(len(cfg.composition.traits)),
                str(len(cfg.composition.rules)),
                str(len(cfg.composition.skills)),
                ", ".join(cfg.priorities) if cfg.priorities else "",
            )
        else:
            table.add_row(name, "?", "?", "?", "")

    console.print(table)


@list_cmd.command("providers")
def list_providers():
    """List available providers."""
    from ..surface_registry import get_surface, surface_names

    for name in sorted(surface_names()):
        provider = get_surface(name)
        sp_path = provider.system_prompt_path(ProjectLayout.at(Path(".")))
        sp_str = str(sp_path) if sp_path is not None else "(session cache)"
        console.print(f"  [cyan]{name}[/]  →  {sp_str}")


@list_cmd.command("traits")
def list_traits():
    """List available traits."""
    from ..models import ComponentType

    asm = _assembler(resolve_project().layout.root, prefer_cwd=True)
    names = asm.resolver.list_components(ComponentType.TRAIT)
    for name in names:
        console.print(f"  [cyan]{name}[/]")


@list_cmd.command("rules")
def list_rules():
    """List available rules."""
    from ..models import ComponentType

    asm = _assembler(resolve_project().layout.root, prefer_cwd=True)
    names = asm.resolver.list_components(ComponentType.RULE)
    for name in names:
        console.print(f"  [cyan]{name}[/]")


@list_cmd.command("skills")
def list_skills():
    """List available skills."""
    from ..models import ComponentType

    asm = _assembler(resolve_project().layout.root, prefer_cwd=True)
    names = asm.resolver.list_components(ComponentType.SKILL)
    for name in names:
        console.print(f"  [cyan]{name}[/]")


@list_cmd.command("tokens")
@click.argument("name")
@click.option(
    "--trait",
    "as_trait",
    is_flag=True,
    default=False,
    help="Analyze as trait instead of role",
)
@click.option(
    "--approx",
    is_flag=True,
    default=False,
    help="Use len//4 instead of Anthropic SDK",
)
def list_tokens(name: str, as_trait: bool, approx: bool):
    """Show token cost breakdown for a role or trait.

    A role is priced as the plan a session would render — overlays applied,
    the same composition ``config show-prompt`` prints. A trait has no
    overlays, so ``--trait`` prices its declared tree.
    """
    from rich.table import Table

    from ..costs import analyze_composition, analyze_plan

    project_dir = resolve_project().layout.root
    if as_trait:
        from ..composer import Composer

        asm = _assembler(project_dir, prefer_cwd=True)
        breakdown = analyze_composition(
            Composer(asm.resolver), name, as_trait=True, exact=not approx
        )
    else:
        from ..composition_seam import build_plan_preview
        from ..diagnostics import emit_to_stderr

        try:
            preview = build_plan_preview(project_dir, role=name)
        except RuntimeError as exc:
            console.print(f"[red]Error[/]: {exc}")
            return
        emit_to_stderr(preview.diagnostics)
        breakdown = analyze_plan(preview.plan, exact=not approx)

    if breakdown.errors:
        for e in breakdown.errors:
            console.print(f"[red]Error[/]: {e}")
        return

    table = Table(title=f"Token costs: {name}", show_footer=True)
    table.add_column("Component", footer="TOTAL")
    table.add_column("Category", style="dim")
    table.add_column("Tokens", justify="right", footer=f"{breakdown.total_tokens:,}")
    table.add_column(
        "Always-on", justify="right", footer=f"[bold]{breakdown.always_on_tokens:,}[/]"
    )
    table.add_column("On-demand", justify="right", footer=f"{breakdown.on_demand_tokens:,}")
    table.add_column(
        "Chars",
        justify="right",
        style="dim",
        footer=f"{sum(c.chars for c in breakdown.components):,}",
    )

    for c in breakdown.components:
        table.add_row(
            _display_name(c.name),
            c.category,
            f"{c.tokens:,}",
            f"[bold]{c.always_on_tokens:,}[/]",
            f"{c.on_demand_tokens:,}",
            f"{c.chars:,}",
        )

    console.print(table)
    method = "anthropic SDK" if breakdown.exact else "approx (len//4)"
    console.print(f"[dim]Method: {method}[/]")


def _display_name(member: str) -> str:
    """The component behind a plan member's full name: the Category column
    already says what kind it is."""
    for suffix in ("::prompt", "::priorities"):
        member = member.removesuffix(suffix)
    for prefix in ("rules::", "skills::"):
        member = member.removeprefix(prefix)
    return member
