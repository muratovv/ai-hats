"""`ai-hats list` — inspect roles/providers/traits/rules/skills in the library."""

from __future__ import annotations

from pathlib import Path

import click

from ._helpers import _assembler, console


@click.group("list")
def list_cmd():
    """List available components."""
    pass


@list_cmd.command("roles")
def list_roles():
    """List available roles."""
    from rich.table import Table

    from ..models import ComponentType

    asm = _assembler()
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
        cfg = asm.resolver.resolve_config(name, ComponentType.ROLE)
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
    from ..providers import get_provider, provider_names

    for name in sorted(provider_names()):
        provider = get_provider(name)
        console.print(f"  [cyan]{name}[/]  →  {provider.system_prompt_path(Path('.'))}")


@list_cmd.command("traits")
def list_traits():
    """List available traits."""
    from ..models import ComponentType

    asm = _assembler()
    names = asm.resolver.list_components(ComponentType.TRAIT)
    for name in names:
        console.print(f"  [cyan]{name}[/]")


@list_cmd.command("rules")
def list_rules():
    """List available rules."""
    from ..models import ComponentType, RuleMetadata

    asm = _assembler()
    names = asm.resolver.list_components(ComponentType.RULE)
    for name in names:
        path = asm.resolver.resolve(name, ComponentType.RULE)
        desc = ""
        if path:
            meta_path = path / "metadata.yaml"
            if meta_path.exists():
                meta = RuleMetadata.from_yaml(meta_path)
                desc = meta.description
        if desc:
            console.print(f"  [cyan]{name}[/]  [dim]{desc}[/]")
        else:
            console.print(f"  [cyan]{name}[/]")


@list_cmd.command("skills")
def list_skills():
    """List available skills."""
    from ..models import ComponentType

    asm = _assembler()
    names = asm.resolver.list_components(ComponentType.SKILL)
    for name in names:
        console.print(f"  [cyan]{name}[/]")


@list_cmd.command("tokens")
@click.argument("name")
@click.option(
    "--trait", "as_trait", is_flag=True, default=False,
    help="Analyze as trait instead of role",
)
@click.option(
    "--approx", is_flag=True, default=False,
    help="Use len//4 instead of Anthropic SDK",
)
def list_tokens(name: str, as_trait: bool, approx: bool):
    """Show token cost breakdown for a role or trait."""
    from rich.table import Table

    from ..composer import Composer
    from ..costs import analyze_composition

    asm = _assembler()
    composer = Composer(asm.resolver)

    breakdown = analyze_composition(composer, name, as_trait=as_trait, exact=not approx)

    if breakdown.errors:
        for e in breakdown.errors:
            console.print(f"[red]Error[/]: {e}")
        return

    table = Table(title=f"Token costs: {name}", show_footer=True)
    table.add_column("Component", footer="TOTAL")
    table.add_column("Category", style="dim")
    table.add_column("Tokens", justify="right", footer=f"[bold]{breakdown.total_tokens:,}[/]")
    table.add_column(
        "Chars",
        justify="right",
        style="dim",
        footer=f"{sum(c.chars for c in breakdown.components):,}",
    )

    for c in breakdown.components:
        table.add_row(c.name, c.category, f"{c.tokens:,}", f"{c.chars:,}")

    console.print(table)
    method = "anthropic SDK" if breakdown.exact else "approx (len//4)"
    console.print(f"[dim]Method: {method}[/]")
