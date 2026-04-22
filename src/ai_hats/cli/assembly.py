"""Role-assembly commands: init, set, customize, status, bump, rollback,
clean, whoami, token-stats."""

from __future__ import annotations

import click
from rich.tree import Tree

from ._helpers import _assembler, _project_dir, console


@click.command()
@click.option("--provider", "-p", default=None, help="Provider (gemini/claude)")
@click.option("--role", "-r", default=None, help="Role to apply after init")
@click.option(
    "--task-prefix", "task_prefix", default=None,
    help="Task-id prefix for `ai-hats task create` (e.g. ACME). "
    "Default: TASK for new projects; auto-detected for legacy repos.",
)
def init(provider: str | None, role: str | None, task_prefix: str | None):
    """Initialize ai-hats in the current directory."""
    project_dir = _project_dir()
    already = (project_dir / "ai-hats.yaml").exists()

    asm = _assembler(project_dir)
    try:
        asm.init(provider=provider, role=role, task_prefix=task_prefix)
    except ValueError as err:
        console.print(f"[red]Error[/]: {err}")
        raise SystemExit(1)

    label = "Re-initialized" if already else "Initialized"
    console.print(f"[green]{label}[/] ai-hats in {project_dir}")

    if role:
        console.print(f"  Role: [bold]{role}[/]")
    console.print(f"  Provider: [bold]{provider or asm.project_config.provider}[/]")
    if task_prefix:
        console.print(f"  Task prefix: [bold]{asm.project_config.task_prefix}[/]")


@click.command("set")
@click.option("--provider", "-p", default=None, help="Provider (gemini/claude)")
@click.option("--role", "-r", default=None, help="Role to apply")
def set_role(provider: str | None, role: str | None):
    """Configure project: set provider and/or role."""
    if not provider and not role:
        console.print("[red]Specify --role/-r and/or --provider/-p[/]")
        raise SystemExit(1)

    project_dir = _project_dir()
    asm = _assembler(project_dir)

    # Auto-init if project not yet initialized
    if not (project_dir / "ai-hats.yaml").exists():
        try:
            asm.init(provider=provider)
        except ValueError as err:
            console.print(f"[red]Error[/]: {err}")
            raise SystemExit(1)
        console.print(f"[green]Initialized[/] ai-hats in {project_dir}")
    elif provider and not role:
        # Provider-only update — validate before persisting, so unknown
        # providers do not get silently written to ai-hats.yaml.
        from ..providers import get_provider

        try:
            get_provider(provider)
        except ValueError as err:
            console.print(f"[red]Error[/]: {err}")
            raise SystemExit(1)
        asm.project_config.provider = provider
        asm.project_config.save(asm.config_path)

    if role:
        try:
            result = asm.set_role(role, provider_name=provider)
        except ValueError as err:
            console.print(f"[red]Error[/]: {err}")
            raise SystemExit(1)
        if result.errors:
            for err in result.errors:
                console.print(f"  [yellow]Warning[/]: {err}")
        console.print(f"[green]Role set[/]: [bold]{result.name}[/]")
        console.print(f"  Rules: {len(result.rules)}")
        console.print(f"  Skills: {len(result.skills)}")
        console.print(f"  Injections: {len(result.injections)}")

    console.print(f"  Provider: [bold]{provider or asm.project_config.provider}[/]")


@click.command()
@click.argument("role")
@click.option("--add-trait", multiple=True, help="Add a trait to the role")
@click.option("--remove-trait", multiple=True, help="Remove a trait from the role")
@click.option("--add-rule", multiple=True, help="Add a rule to the role")
@click.option("--remove-rule", multiple=True, help="Remove a rule from the role")
@click.option("--add-skill", multiple=True, help="Add a skill to the role")
@click.option("--remove-skill", multiple=True, help="Remove a skill from the role")
@click.option("--injection-append", default=None, help="Append injection text")
@click.option("--show", "show_only", is_flag=True, help="Show current customizations")
@click.option("--reset", "do_reset", is_flag=True, help="Remove all customizations for role")
def customize(
    role: str,
    add_trait: tuple,
    remove_trait: tuple,
    add_rule: tuple,
    remove_rule: tuple,
    add_skill: tuple,
    remove_skill: tuple,
    injection_append: str | None,
    show_only: bool,
    do_reset: bool,
):
    """Customize a role: add/remove traits, rules, skills."""
    from ..models import OverlayConfig, ProjectConfig

    project_dir = _project_dir()
    config_path = project_dir / "ai-hats.yaml"
    if not config_path.exists():
        console.print("[red]No ai-hats.yaml found[/]. Run: ai-hats set -r <role> -p <provider>")
        raise SystemExit(1)

    config = ProjectConfig.from_yaml(config_path)

    if do_reset:
        config.customizations.pop(role, None)
        config.save(config_path)
        console.print(f"[green]Reset[/] customizations for [bold]{role}[/]")
        return

    overlay = config.customizations.get(role, OverlayConfig())

    if show_only:
        if overlay.is_empty:
            console.print(f"[dim]No customizations for {role}[/]")
        else:
            import yaml

            console.print(f"[bold]{role}[/] customizations:")
            console.print(yaml.dump(overlay.to_dict(), default_flow_style=False).rstrip())
        return

    has_changes = any(
        [add_trait, remove_trait, add_rule, remove_rule, add_skill, remove_skill, injection_append]
    )
    if not has_changes:
        console.print("[yellow]No changes specified[/]. Use --add-trait, --remove-trait, etc.")
        return

    # Merge new values into existing overlay
    for t in add_trait:
        if t not in overlay.add_traits:
            overlay.add_traits.append(t)
        # If previously removed, undo
        if t in overlay.remove_traits:
            overlay.remove_traits.remove(t)
    for t in remove_trait:
        if t not in overlay.remove_traits:
            overlay.remove_traits.append(t)
        if t in overlay.add_traits:
            overlay.add_traits.remove(t)
    for r in add_rule:
        if r not in overlay.add_rules:
            overlay.add_rules.append(r)
        if r in overlay.remove_rules:
            overlay.remove_rules.remove(r)
    for r in remove_rule:
        if r not in overlay.remove_rules:
            overlay.remove_rules.append(r)
        if r in overlay.add_rules:
            overlay.add_rules.remove(r)
    for s in add_skill:
        if s not in overlay.add_skills:
            overlay.add_skills.append(s)
        if s in overlay.remove_skills:
            overlay.remove_skills.remove(s)
    for s in remove_skill:
        if s not in overlay.remove_skills:
            overlay.remove_skills.append(s)
        if s in overlay.add_skills:
            overlay.add_skills.remove(s)
    if injection_append is not None:
        overlay.injection_append = injection_append

    config.customizations[role] = overlay
    config.save(config_path)
    console.print(f"[green]Updated[/] customizations for [bold]{role}[/]")
    import yaml

    console.print(yaml.dump(overlay.to_dict(), default_flow_style=False).rstrip())


@click.command()
def status():
    """Show current role, dependency tree, and health."""
    asm = _assembler()
    st = asm.status()

    if not st["role"]:
        console.print("[yellow]No role active[/]")
        return

    console.print(f"Role: [bold]{st['role']}[/]")
    console.print(f"Provider: {st['provider']}")

    # Dependency tree
    if st["tree"]:
        tree = Tree(f"[bold]{st['tree']['name']}[/]")
        if st["tree"]["priorities"]:
            p_branch = tree.add("[dim]priorities[/]")
            for p in st["tree"]["priorities"]:
                p_branch.add(p)
        if st["tree"]["rules"]:
            r_branch = tree.add("[dim]rules[/]")
            for r in st["tree"]["rules"]:
                r_branch.add(r)
        if st["tree"]["skills"]:
            s_branch = tree.add("[dim]skills[/]")
            for s in st["tree"]["skills"]:
                s_branch.add(s)
        if st["tree"]["hooks"]:
            h_branch = tree.add("[dim]hooks[/]")
            for event, scripts in st["tree"]["hooks"].items():
                h_branch.add(f"{event}: {scripts}")
        if st["tree"]["mcp"]:
            m_branch = tree.add("[dim]mcp[/]")
            for m in st["tree"]["mcp"]:
                m_branch.add(m)
        console.print(tree)

    # Health
    if st.get("health"):
        console.print("\n[bold]Health:[/]")
        for component, status_val in st["health"].items():
            icon = "[green]OK[/]" if status_val == "OK" else "[red]Missing[/]"
            console.print(f"  {component}: {icon}", highlight=False)

    if st.get("errors"):
        console.print("\n[bold yellow]Errors:[/]")
        for err in st["errors"]:
            console.print(f"  [yellow]{err}[/]")


@click.command()
def bump():
    """Update to latest component versions (re-assemble)."""
    asm = _assembler()
    result = asm.bump()
    if result is None:
        console.print("[yellow]No role active to bump[/]")
        return
    console.print(f"[green]Bumped[/]: {result.name}")


@click.command()
def rollback():
    """Rollback to previous state."""
    asm = _assembler()
    if asm.rollback():
        console.print("[green]Rolled back[/] to previous state")
    else:
        console.print("[yellow]No backup available[/]")


@click.command()
def clean():
    """Clean active directories."""
    asm = _assembler()
    asm.clean()
    console.print("[green]Cleaned[/] .agent/ directories")


@click.command()
def whoami():
    """Show diagnostic session info."""
    asm = _assembler()
    info = asm.whoami()
    for k, v in info.items():
        console.print(f"  {k}: [bold]{v}[/]")


@click.command("token-stats")
@click.argument("name")
@click.option(
    "--trait", "as_trait", is_flag=True, default=False, help="Analyze as trait instead of role"
)
@click.option("--approx", is_flag=True, default=False, help="Use len//4 instead of Anthropic SDK")
def token_stats(name: str, as_trait: bool, approx: bool):
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
