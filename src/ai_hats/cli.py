"""CLI interface — Click-based command-line tool."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.tree import Tree

from . import __version__

console = Console()


def _project_dir() -> Path:
    return Path.cwd()


def _assembler(project_dir: Path | None = None):
    from .assembler import Assembler

    return Assembler(project_dir or _project_dir())


class _PassthroughGroup(click.Group):
    """Click group that treats unknown flag-like leftover args as extras
    instead of failing with 'No such command'. HATS-087.

    Click 8.x splits the parser leftover into ``ctx._protected_args[:1]``
    (the candidate subcommand name) and ``ctx.args[1:]``. If the first
    leftover token starts with ``-``, it is a flag the user wants
    forwarded to the underlying provider, NOT a subcommand. This override
    moves those tokens back into ``ctx.args`` so the no-subcommand path
    runs and the bare ``def main(ctx, ...)`` body sees them.

    No-op on click 9.x where ``_protected_args`` is removed and ``args``
    already contains all leftover tokens — the ``getattr`` defensiveness
    handles the absence gracefully.

    Caveat: subcommands whose name starts with ``-`` would be mis-routed.
    The project has none today; if one is added, this override needs
    updating.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        result = super().parse_args(ctx, args)
        protected = getattr(ctx, "_protected_args", None)
        if protected and protected[0].startswith("-"):
            ctx.args = list(protected) + list(ctx.args)
            ctx._protected_args = []
        return result


@click.group(
    cls=_PassthroughGroup,
    invoke_without_command=True,
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
        "allow_interspersed_args": False,
    },
)
@click.version_option(version=__version__)
@click.option("--provider", "-p", default=None, help="Provider override (gemini/claude)")
@click.option("--role", "-r", default=None, help="Role override")
@click.pass_context
def main(ctx, provider: str | None, role: str | None):
    """ai-hats — AI agent role composition framework.

    Without a subcommand, launches a wrapped provider CLI session.
    Unknown flags are passed through to the provider.
    """
    if ctx.invoked_subcommand is None:
        _launch_session(provider=provider, role=role, extra_args=ctx.args)


# -- init --


@main.command()
@click.option("--provider", "-p", default=None, help="Provider (gemini/claude)")
@click.option("--role", "-r", default=None, help="Role to apply after init")
def init(provider: str | None, role: str | None):
    """Initialize ai-hats in the current directory."""
    project_dir = _project_dir()
    already = (project_dir / "ai-hats.yaml").exists()

    asm = _assembler(project_dir)
    asm.init(provider=provider, role=role)

    label = "Re-initialized" if already else "Initialized"
    console.print(f"[green]{label}[/] ai-hats in {project_dir}")

    if role:
        console.print(f"  Role: [bold]{role}[/]")
    console.print(f"  Provider: [bold]{provider or asm.project_config.provider}[/]")


# -- set --


@main.command("set")
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
        asm.init(provider=provider)
        console.print(f"[green]Initialized[/] ai-hats in {project_dir}")
    elif provider and not role:
        # Provider-only update
        asm.project_config.provider = provider
        asm.project_config.save(asm.config_path)

    if role:
        result = asm.set_role(role, provider_name=provider)
        if result.errors:
            for err in result.errors:
                console.print(f"  [yellow]Warning[/]: {err}")
        console.print(f"[green]Role set[/]: [bold]{result.name}[/]")
        console.print(f"  Rules: {len(result.rules)}")
        console.print(f"  Skills: {len(result.skills)}")
        console.print(f"  Injections: {len(result.injections)}")

    console.print(f"  Provider: [bold]{provider or asm.project_config.provider}[/]")


# -- customize --


@main.command()
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
    from .models import OverlayConfig, ProjectConfig

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


# -- status --


@main.command()
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


# -- bump --


@main.command()
def bump():
    """Update to latest component versions (re-assemble)."""
    asm = _assembler()
    result = asm.bump()
    if result is None:
        console.print("[yellow]No role active to bump[/]")
        return
    console.print(f"[green]Bumped[/]: {result.name}")


# -- rollback --


@main.command()
def rollback():
    """Rollback to previous state."""
    asm = _assembler()
    if asm.rollback():
        console.print("[green]Rolled back[/] to previous state")
    else:
        console.print("[yellow]No backup available[/]")


# -- clean --


@main.command()
def clean():
    """Clean active directories."""
    asm = _assembler()
    asm.clean()
    console.print("[green]Cleaned[/] .agent/ directories")


# -- list --


@main.group("list")
def list_cmd():
    """List available components."""
    pass


@list_cmd.command("roles")
def list_roles():
    """List available roles."""
    from rich.table import Table

    from .models import ComponentType

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
    from .providers import PROVIDERS

    for name in sorted(PROVIDERS):
        provider = PROVIDERS[name]()
        console.print(f"  [cyan]{name}[/]  →  {provider.system_prompt_path(Path('.'))}")


@list_cmd.command("traits")
def list_traits():
    """List available traits."""
    from .models import ComponentType

    asm = _assembler()
    names = asm.resolver.list_components(ComponentType.TRAIT)
    for name in names:
        console.print(f"  [cyan]{name}[/]")


@list_cmd.command("rules")
def list_rules():
    """List available rules."""
    from .models import ComponentType, RuleMetadata

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
    from .models import ComponentType

    asm = _assembler()
    names = asm.resolver.list_components(ComponentType.SKILL)
    for name in names:
        console.print(f"  [cyan]{name}[/]")


# -- whoami --


@main.command()
def whoami():
    """Show diagnostic session info."""
    asm = _assembler()
    info = asm.whoami()
    for k, v in info.items():
        console.print(f"  {k}: [bold]{v}[/]")


# -- token-stats --


@main.command("token-stats")
@click.argument("name")
@click.option(
    "--trait", "as_trait", is_flag=True, default=False, help="Analyze as trait instead of role"
)
@click.option("--approx", is_flag=True, default=False, help="Use len//4 instead of Anthropic SDK")
def token_stats(name: str, as_trait: bool, approx: bool):
    """Show token cost breakdown for a role or trait."""
    from rich.table import Table

    from .composer import Composer
    from .costs import analyze_composition

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


def _launch_session(
    provider: str | None = None,
    role: str | None = None,
    extra_args: list[str] | None = None,
):
    """Launch a wrapped provider CLI session."""
    from .models import ProfileConfig, ProjectConfig
    from .runtime import WrapRunner

    project_dir = _project_dir()
    profile = ProfileConfig.load(project_dir / "profile.json")
    config = ProjectConfig.from_yaml(project_dir / "ai-hats.yaml")

    effective_provider = provider or profile.provider or config.provider
    if not effective_provider:
        console.print("[red]No provider configured[/]. Run: ai-hats set -p <provider>")
        sys.exit(1)

    runner = WrapRunner(project_dir)
    exit_code = runner.run(
        effective_provider,
        role_override=role,
        extra_args=extra_args or None,
    )
    sys.exit(exit_code)


# -- run --


@main.command("run")
@click.argument("role")
@click.option("--ticket", default=None, help="Ticket/task ID for context")
@click.option("--model", default=None, help="Model override")
@click.option("--task", default=None, help="Task description")
@click.option(
    "--isolation",
    default="discard",
    type=click.Choice(["discard", "squash", "branch"]),
    help="Worktree isolation mode (default: discard)",
)
def run_subagent(
    role: str, ticket: str | None, model: str | None, task: str | None, isolation: str
):
    """Run a sub-agent with the given role."""
    from .runtime import SubAgentRunner

    runner = SubAgentRunner(_project_dir())
    session = runner.run(
        role_name=role,
        task=task or "",
        ticket_id=ticket or "",
        model=model or "",
        isolation_mode=isolation,
    )
    console.print(f"[green]Sub-agent completed[/]: {session.session_id}")
    console.print(f"  Session dir: {session.session_dir}")


# -- wt (worktree) --


def _resolve_worktree(branch: str | None = None):
    """Resolve a WorktreeManager from branch arg, CWD, or first active.

    Returns None when nothing can be found.
    """
    import subprocess as _sp

    from .worktree import WorktreeManager

    project_dir = _project_dir()

    if branch is not None:
        return WorktreeManager.load_for_branch(project_dir, branch)

    # CWD is inside a linked worktree → detect branch automatically.
    if WorktreeManager.is_inside_linked_worktree(project_dir):
        try:
            head = _sp.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except _sp.CalledProcessError:
            return None
        return WorktreeManager.load_for_branch(project_dir, head)

    # Fallback: first active worktree.
    active = WorktreeManager.list_active(project_dir)
    return active[0] if active else None


@main.group()
def wt():
    """Manage git worktrees for isolated work."""
    pass


@wt.command("create")
@click.argument("branch")
def wt_create(branch: str):
    """Create an isolated worktree on a new branch."""
    from .worktree import WorktreeManager

    project_dir = _project_dir()

    # HATS-060: refuse to create from inside a linked worktree.
    if WorktreeManager.is_inside_linked_worktree(project_dir):
        console.print("[red]Cannot create a worktree from inside a linked worktree[/]")
        console.print(f"  You are in: {project_dir}")
        console.print("  Run [bold]ai-hats wt create[/] from the main repo.")
        sys.exit(1)

    # HATS-061: check if this specific branch already has a worktree.
    existing = WorktreeManager.load_for_branch(project_dir, branch)
    if existing is not None:
        console.print(f"[red]Worktree already exists for branch[/]: {existing.branch_name}")
        console.print(f"  Path: {existing.worktree_path}")
        sys.exit(1)

    mgr = WorktreeManager(project_dir, branch_name=branch)
    wt_path = mgr.create()
    mgr.save_state()
    console.print(f"[green]Worktree created[/]: {branch}")
    console.print(f"  Path: {wt_path}")
    console.print(f"  [dim]cd {wt_path}[/]")


@wt.command("merge")
@click.argument("branch", required=False)
@click.option("--squash", is_flag=True, default=False, help="Squash all commits into one")
@click.option("--force", is_flag=True, default=False, help="Merge even with uncommitted changes")
def wt_merge(branch: str | None, squash: bool, force: bool):
    """Merge worktree changes back and clean up.

    Without BRANCH: auto-detect from CWD (if inside a linked worktree).
    Default: --no-ff merge preserving commit history.
    Refuses if worktree has uncommitted changes (use --force to override).
    """
    from .worktree import WorktreeDirtyError

    mgr = _resolve_worktree(branch)
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        if branch is None:
            console.print("  Specify a branch: [bold]ai-hats wt merge <branch>[/]")
        sys.exit(1)

    name = mgr.branch_name
    try:
        mgr.merge(squash=squash, force=force)
    except WorktreeDirtyError as e:
        console.print(f"[red]Refused[/]: {e}")
        sys.exit(1)
    console.print(f"[green]Merged[/]: {name}")


@wt.command("discard")
@click.argument("branch", required=False)
@click.option("--force", is_flag=True, default=False, help="Discard even with uncommitted changes")
def wt_discard(branch: str | None, force: bool):
    """Discard worktree changes and clean up.

    Without BRANCH: auto-detect from CWD (if inside a linked worktree).
    Refuses if worktree has uncommitted changes (use --force to override).
    """
    from .worktree import WorktreeDirtyError

    mgr = _resolve_worktree(branch)
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        if branch is None:
            console.print("  Specify a branch: [bold]ai-hats wt discard <branch>[/]")
        sys.exit(1)

    name = mgr.branch_name
    try:
        mgr.discard(force=force)
    except WorktreeDirtyError as e:
        console.print(f"[red]Refused[/]: {e}")
        sys.exit(1)
    console.print(f"[green]Discarded[/]: {name}")


@wt.command("list")
def wt_list():
    """List all git worktrees."""
    from .worktree import WorktreeManager

    project_dir = _project_dir()
    worktrees = WorktreeManager.list_worktrees(project_dir)
    tracked_branches = {m.branch_name for m in WorktreeManager.list_active(project_dir)}

    if not worktrees:
        console.print("[dim]No worktrees[/]")
        return

    for w in worktrees:
        branch = w.get("branch", "?")
        path = w.get("path", "?")
        marker = " [green]← tracked[/]" if branch in tracked_branches else ""
        console.print(f"  {branch}: {path}{marker}")


@wt.command("status")
def wt_status():
    """Show all tracked worktrees."""
    from .worktree import WorktreeManager

    active = WorktreeManager.list_active(_project_dir())
    if not active:
        console.print("[dim]No active worktrees[/]")
        return

    for mgr in active:
        console.print(f"  Branch: [bold]{mgr.branch_name}[/]  Path: {mgr.worktree_path}")


@wt.command("exec", context_settings={"ignore_unknown_options": True})
@click.argument("cmd_args", nargs=-1, type=click.UNPROCESSED, required=True)
def wt_exec(cmd_args: tuple[str, ...]):
    """Run a command inside the active worktree (cwd + PYTHONPATH=src).

    Replaces gnarly shell boilerplate like:

    \b
        WT=/var/folders/.../ai-hats-wt-...
        PYTHONPATH=$WT/src python -m pytest tests/test_foo.py -xvs

    Use `--` to separate ai-hats args from the inner command:

    \b
        ai-hats wt exec -- pytest tests/test_foo.py -xvs
        ai-hats wt exec -- python -c 'import ai_hats; print(ai_hats.__file__)'
        ai-hats wt exec -- ruff check src/
    """
    mgr = _resolve_worktree()
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        sys.exit(1)

    wt_path = mgr.worktree_path
    if wt_path is None:
        console.print("[red]Active worktree has no path[/]")
        sys.exit(1)

    env = os.environ.copy()
    src_path = str(wt_path / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path

    try:
        result = subprocess.run(list(cmd_args), cwd=str(wt_path), env=env)
    except FileNotFoundError as e:
        console.print(f"[red]Command not found:[/] {e.filename}")
        sys.exit(127)
    sys.exit(result.returncode)


@wt.command("env")
def wt_env():
    """Print shell exports for the active worktree (eval-friendly).

    Usage:

    \b
        eval "$(ai-hats wt env)"
        # now $WT and $PYTHONPATH are set; cd to it manually if needed
    """
    mgr = _resolve_worktree()
    if mgr is None:
        click.echo("# no active worktree", err=True)
        sys.exit(1)

    wt_path = mgr.worktree_path
    if wt_path is None:
        click.echo("# active worktree has no path", err=True)
        sys.exit(1)

    click.echo(f'export WT="{wt_path}"')
    click.echo(f'export PYTHONPATH="{wt_path}/src${{PYTHONPATH:+:$PYTHONPATH}}"')


# -- judge --


@main.command()
@click.option("--bundle", "bundle_id", default=None, help="Bundle id to judge")
@click.option("--sessions", default=None, help="Comma-separated session ids (auto-bundle)")
@click.option(
    "--last", "last_n", default=None, type=int, help="Judge last N sessions (auto-bundle)"
)
@click.option("--focus", default=None, help="Focus lens for the judge")
def judge(
    bundle_id: str | None,
    sessions: str | None,
    last_n: int | None,
    focus: str | None,
):
    """Spawn judge sub-agent over a bundle and validate its output."""
    project_dir = _project_dir()

    from .retro.judge import JudgeRunner, JudgeValidationError

    runner = JudgeRunner(project_dir)
    session_ids = [s.strip() for s in sessions.split(",")] if sessions else None

    # Show what we're about to judge
    _print_judge_context(runner, bundle_id, session_ids, last_n, focus)

    label = bundle_id or (
        f"sessions={','.join(session_ids)}" if session_ids else f"last={last_n}" if last_n else "?"
    )
    try:
        with console.status(
            f"[cyan]Judging {label} (spawning judge sub-agent, may take a few minutes)...[/]",
            spinner="dots",
        ):
            path = runner.judge(
                bundle_id=bundle_id,
                session_ids=session_ids,
                last_n=last_n,
                focus=focus,
            )
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    except JudgeValidationError as exc:
        console.print(f"[red]Judge output failed validation[/]:\n{exc}")
        sys.exit(2)
    console.print(f"[green]Judge retro[/]: {path}")


def _print_judge_context(
    runner, bundle_id: str | None, session_ids: list[str] | None,
    last_n: int | None, focus: str | None,
) -> None:
    """Show bundle/session info before judging starts."""
    try:
        if bundle_id:
            bundle = runner.bundles.get(bundle_id)
            sids = bundle.session_ids
        elif session_ids:
            sids = session_ids
        elif last_n:
            from .observe import SessionManager
            sessions = SessionManager(runner.project_dir).list_sessions(last_n=last_n)
            sids = [s.session_id for s in sessions]
        else:
            return
    except (FileNotFoundError, ValueError):
        return

    console.print("[bold]Judge run[/]")
    console.print(f"  Sessions ({len(sids)}):")
    for sid in sids:
        console.print(f"    - {sid}")
    if focus:
        console.print(f"  Focus: [cyan]{focus}[/]")
    console.print()


@main.command("judge-aggregate")
@click.option(
    "--strategy",
    type=click.Choice(["freq"]),
    default="freq",
    help="Aggregation strategy (default: freq)",
)
@click.option("--since", default=None, help="Only include retros since YYYY-MM-DD")
@click.option(
    "--min-severity",
    type=click.Choice(["low", "medium", "high", "critical"]),
    default=None,
    help="Exclude findings below this severity",
)
def judge_aggregate(strategy: str, since: str | None, min_severity: str | None):
    """Aggregate judge retros to surface recurring patterns."""
    from datetime import date as date_cls

    from .retro.aggregator import Aggregator
    from .retro.common import Severity

    since_date = date_cls.fromisoformat(since) if since else None
    sev = Severity(min_severity) if min_severity else None

    agg = Aggregator(_project_dir())
    try:
        path = agg.aggregate(strategy=strategy, since=since_date, min_severity=sev)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)

    from .retro.loader import load

    model, body = load(path)
    console.print(f"[green]Aggregation saved[/]: {path}")
    console.print(body)


# -- retro --


@main.command()
@click.argument("session_id", required=False)
@click.option("--last", "use_last", is_flag=True, help="Use the most recent session")
@click.option(
    "--mode",
    type=click.Choice(["programmatic", "llm"]),
    default="programmatic",
    help="Builder mode: programmatic (fast, no LLM) or llm (narrative summary)",
)
@click.option(
    "--timeout",
    default=600,
    type=int,
    help="LLM call timeout in seconds (llm/hybrid only, default 600)",
)
def retro(session_id: str | None, use_last: bool, mode: str, timeout: int):
    """Generate a structured session retrospective (HATS-051 schema)."""
    from .observe import SessionManager
    from .retro.builder import BuilderMode, SessionRetroBuilder
    from .retro.llm_caller import SubprocessLLMCaller

    project_dir = _project_dir()
    if use_last or not session_id:
        sessions = SessionManager(project_dir).list_sessions(last_n=1)
        if not sessions:
            console.print("[red]No sessions found[/]")
            sys.exit(1)
        session_id = sessions[0].session_id

    builder_mode = BuilderMode(mode)
    use_llm = builder_mode == BuilderMode.LLM
    llm_caller = SubprocessLLMCaller(project_dir, timeout=timeout) if use_llm else None
    builder = SessionRetroBuilder(project_dir, llm_caller=llm_caller)

    def _do_build() -> Path:
        return builder.build_and_save(session_id, mode=builder_mode)

    try:
        if use_llm:
            with console.status(
                f"[cyan]Generating retrospective for {session_id} "
                f"(mode={mode}, timeout={timeout}s, calling LLM)...[/]",
                spinner="dots",
            ):
                path = _do_build()
        else:
            path = _do_build()
    except FileNotFoundError as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        console.print(f"[red]LLM call failed[/]: {exc}")
        console.print("[dim]Tip: try --timeout 600 or fall back to --mode programmatic[/]")
        sys.exit(1)
    console.print(f"[green]Session retro[/]: {path}")


# -- bundle --


@main.group()
def bundle():
    """Manage session bundles for judge analysis."""


@bundle.command("create")
@click.option("--sessions", default=None, help="Comma-separated session ids")
@click.option("--last", "last_n", default=None, type=int, help="Use last N sessions")
@click.option("--since", default=None, help="Use sessions since YYYY-MM-DD")
@click.option("--unreviewed", is_flag=True, help="Use all productive sessions not yet in any bundle")
@click.option("--min-turns", default=0, type=int, help="With --unreviewed: minimum turns filter")
@click.option("--notes", default=None, help="Free-form notes")
def bundle_create(
    sessions: str | None,
    last_n: int | None,
    since: str | None,
    unreviewed: bool,
    min_turns: int,
    notes: str | None,
):
    """Create a new bundle artifact (lens-agnostic; pass --focus on `judge`)."""
    import json
    from datetime import date as _date

    from .retro.bundles import BundleManager

    bm = BundleManager(_project_dir())
    try:
        if unreviewed:
            from .observe import SessionManager
            all_sessions = SessionManager(_project_dir()).list_sessions(productive_only=True)
            reviewed = bm.reviewed_session_ids()
            ids = []
            for s in all_sessions:
                if s.session_id in reviewed:
                    continue
                if min_turns > 0 and s.metrics_path.exists():
                    try:
                        m = json.loads(s.metrics_path.read_text())
                        if m.get("turns", 0) < min_turns:
                            continue
                    except (json.JSONDecodeError, OSError):
                        continue
                ids.append(s.session_id)
            if not ids:
                console.print("[yellow]No unreviewed sessions found[/]")
                sys.exit(0)
            b = bm.create(ids, notes=notes)
        elif sessions:
            ids = [s.strip() for s in sessions.split(",") if s.strip()]
            b = bm.create(ids, notes=notes)
        elif last_n:
            b = bm.create_from_last(last_n, notes=notes)
        elif since:
            b = bm.create_from_since(_date.fromisoformat(since), notes=notes)
        else:
            console.print("[red]Specify one of: --sessions, --last, --since, --unreviewed[/]")
            sys.exit(1)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    console.print(f"[green]Bundle[/]: {b.bundle_id}")
    console.print(f"  Sessions: {len(b.session_ids)}")


@bundle.command("list")
def bundle_list():
    """List existing bundles."""
    from .retro.bundles import BundleManager

    bm = BundleManager(_project_dir())
    bundles = bm.list()
    if not bundles:
        console.print("[dim]No bundles[/]")
        return
    for b in bundles:
        notes = f" — {b.notes}" if b.notes else ""
        console.print(f"  {b.bundle_id}  ({len(b.session_ids)} session(s)){notes}")


@bundle.command("show")
@click.argument("bundle_id")
def bundle_show(bundle_id: str):
    """Show contents of one bundle."""
    from .retro.bundles import BundleManager

    bm = BundleManager(_project_dir())
    try:
        b = bm.get(bundle_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    console.print(f"[bold]{b.bundle_id}[/]")
    console.print(f"  Project: {b.project}")
    console.print(f"  Created: {b.created.isoformat()}")
    if b.notes:
        console.print(f"  Notes: {b.notes}")
    console.print("  Sessions:")
    for sid in b.session_ids:
        console.print(f"    - {sid}")


# -- audit --


@main.command()
@click.option("--session", default=None, help="Session ID to audit")
def audit(session: str | None):
    """Show audit for a session."""
    from .observe import SessionManager

    mgr = SessionManager(_project_dir())

    if session:
        s = mgr.get_session(session)
    else:
        sessions = mgr.list_sessions(last_n=1)
        s = sessions[0] if sessions else None

    if s is None:
        console.print("[yellow]No session found[/]")
        return

    if s.audit_path.exists():
        console.print(s.audit_path.read_text())
    else:
        console.print(f"[yellow]No audit for session {s.session_id}[/]")


# -- session --


@main.group()
def session():
    """Browse and inspect sessions."""


@session.command("list")
@click.option("--last", "last_n", default=20, type=int, help="Show last N sessions (default 20)")
@click.option("--all", "show_all", is_flag=True, help="Show all sessions")
@click.option("--min-turns", default=0, type=int, help="Only sessions with >= N turns")
@click.option("--productive", is_flag=True, help="Only productive sessions (turns>0, tools>0)")
@click.option("--unreviewed", is_flag=True, help="Only sessions not yet in any bundle")
def session_list(last_n: int, show_all: bool, min_turns: int, productive: bool, unreviewed: bool):
    """List sessions with key metrics."""
    import json

    from .observe import SessionManager

    mgr = SessionManager(_project_dir())
    sessions = mgr.list_sessions(productive_only=productive)

    if not sessions:
        console.print("[yellow]No sessions found[/]")
        return

    # Filter by unreviewed
    if unreviewed:
        from .retro.bundles import BundleManager
        reviewed = BundleManager(_project_dir()).reviewed_session_ids()
        sessions = [s for s in sessions if s.session_id not in reviewed]

    # Filter by min-turns
    if min_turns > 0:
        filtered = []
        for s in sessions:
            if s.metrics_path.exists():
                try:
                    m = json.loads(s.metrics_path.read_text())
                    if m.get("turns", 0) >= min_turns:
                        filtered.append(s)
                except (json.JSONDecodeError, OSError):
                    pass
        sessions = filtered

    if not show_all:
        sessions = sessions[-last_n:]

    from rich.table import Table

    table = Table(show_header=True, header_style="bold")
    table.add_column("Date", style="dim")
    table.add_column("Session ID", style="cyan")
    table.add_column("Role")
    table.add_column("Provider")
    table.add_column("Turns", justify="right")
    table.add_column("Tools", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Tokens out", justify="right")

    def _session_date(sid: str) -> str:
        try:
            return f"{sid[:4]}-{sid[4:6]}-{sid[6:8]}"
        except (IndexError, ValueError):
            return "?"

    for s in sessions:
        date_str = _session_date(s.session_id)
        if not s.metrics_path.exists():
            table.add_row(date_str, s.session_id, "?", "?", "?", "?", "?", "?")
            continue
        try:
            m = json.loads(s.metrics_path.read_text())
        except (json.JSONDecodeError, OSError):
            table.add_row(date_str, s.session_id, "?", "?", "?", "?", "?", "?")
            continue

        role = m.get("role", "?")
        provider = m.get("provider", "?")
        turns = m.get("turns", "?")
        tools = m.get("tool_calls", "?")
        tokens = m.get("tokens", {})
        tok_out = tokens.get("output", "?")
        duration = m.get("duration_wall_minutes")
        if duration is None:
            # Try to parse from audit header
            dur_str = "?"
            if s.audit_path.exists():
                header = s.audit_path.read_text()[:500]
                import re
                dur_m = re.search(r"Duration: (\d+m \d+s)", header)
                if dur_m:
                    dur_str = dur_m.group(1)
            duration = dur_str
        else:
            duration = f"{int(duration)}m"

        tok_out_str = f"{tok_out:,}" if isinstance(tok_out, int) else str(tok_out)

        table.add_row(
            date_str, s.session_id, str(role), str(provider),
            str(turns), str(tools), str(duration), tok_out_str,
        )

    console.print(table)
    console.print(f"[dim]{len(sessions)} sessions shown[/]")


@session.command("show")
@click.argument("session_id")
def session_show(session_id: str):
    """Show detailed metrics for a session."""
    import json

    from .observe import SessionManager

    mgr = SessionManager(_project_dir())
    s = mgr.get_session(session_id)
    if s is None:
        console.print(f"[red]Session {session_id} not found[/]")
        sys.exit(1)

    console.print(f"[bold]Session:[/] {s.session_id}")
    console.print(f"[bold]Path:[/] {s.session_dir}")

    if s.metrics_path.exists():
        try:
            m = json.loads(s.metrics_path.read_text())
            console.print("\n[bold]Metrics:[/]")
            for k, v in m.items():
                if isinstance(v, dict):
                    console.print(f"  {k}:")
                    for k2, v2 in v.items():
                        console.print(f"    {k2}: {v2}")
                else:
                    console.print(f"  {k}: {v}")
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[yellow]Cannot read metrics: {e}[/]")

    artifacts = []
    for name in ("audit.md", "metrics.json", "trace.log", "transcript.txt", "reasoning.log", "meta_prompt.txt"):
        p = s.session_dir / name
        if p.exists() and p.stat().st_size > 0:
            artifacts.append(f"{name} ({p.stat().st_size:,}b)")
    if artifacts:
        console.print(f"\n[bold]Artifacts:[/] {', '.join(artifacts)}")


# -- retro schema utilities --


@main.command("retro-validate")
@click.argument(
    "paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def retro_validate(paths: tuple[Path, ...]) -> None:
    """Validate one or more retro files (session-retro / bundle / judge-retro)."""
    from .retro.loader import load

    failures = 0
    for path in paths:
        try:
            model, _ = load(path)
            family = type(model).__name__
            console.print(f"[green]OK[/] {path} [dim]({family})[/]")
        except Exception as exc:
            failures += 1
            console.print(f"[red]FAIL[/] {path}")
            console.print(f"  [dim]{type(exc).__name__}: {exc}[/]")

    if failures:
        console.print(f"\n[red]{failures}/{len(paths)} files failed validation[/]")
        sys.exit(1)


@main.command("retro-migrate")
@click.argument(
    "paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--dry-run", is_flag=True, help="Show what would change without writing")
def retro_migrate(paths: tuple[Path, ...], dry_run: bool) -> None:
    """Migrate retro files in-place to the latest schema for their family.

    No-op for files already at the latest version. Validates the migrated
    output before writing back.
    """
    from .retro.loader import SCHEMA_FAMILY_TO_MODEL, parse
    from .retro.migrations import family_of, migrate_to_latest
    from .retro.writer import dump

    changed = 0
    for path in paths:
        try:
            raw, body = parse(path.read_text())
            before_version = raw.get("schema", "<missing>")
            migrated = migrate_to_latest(raw)
            after_version = migrated["schema"]

            if before_version == after_version:
                console.print(f"[dim]·[/] {path} [dim](already {after_version})[/]")
                continue

            model_cls = SCHEMA_FAMILY_TO_MODEL[family_of(after_version)]
            model = model_cls.model_validate(migrated)

            if dry_run:
                console.print(
                    f"[yellow]WOULD MIGRATE[/] {path}: {before_version} → {after_version}"
                )
            else:
                dump(model, path, body=body)
                console.print(f"[green]MIGRATED[/] {path}: {before_version} → {after_version}")
                changed += 1
        except Exception as exc:
            console.print(f"[red]FAIL[/] {path}: {type(exc).__name__}: {exc}")
            sys.exit(1)

    if not dry_run and changed:
        console.print(f"\n[green]{changed}/{len(paths)} files migrated[/]")


# -- task management --


@main.group()
def task():
    """Manage task cards and state machine."""
    pass


@task.command("create")
@click.argument("title")
@click.option("--id", "task_id", default=None, help="Task ID (auto-generated if omitted)")
@click.option("--description", "-d", default="", help="Task description")
@click.option("--priority", "-p", default="medium", help="Priority (low/medium/high)")
@click.option("--role", default="", help="Assigned role")
@click.option("--reviewer", default="user", help="Reviewer (user or agent)")
@click.option("--tag", multiple=True, help="Tags")
def task_create(
    task_id: str | None,
    title: str,
    description: str,
    priority: str,
    role: str,
    reviewer: str,
    tag: tuple,
):
    """Create a new task card. ID is auto-generated if omitted."""
    from .state import TaskManager

    mgr = TaskManager(_project_dir())
    if task_id is None:
        task_id = mgr.next_id()
    t = mgr.create_task(
        task_id,
        title,
        description=description,
        priority=priority,
        role=role,
        reviewer=reviewer,
        tags=list(tag),
    )
    console.print(f"[green]Created[/]: {t.id} — {t.title} [{t.state.value}] ({t.priority})")


@task.command("transition")
@click.argument("task_id")
@click.argument("new_state")
@click.option(
    "--final-state", default=None, help="Final accomplished state (for review transition)"
)
def task_transition(task_id: str, new_state: str, final_state: str | None):
    """Transition a task to a new state."""
    from .models import TaskState
    from .state import TaskManager

    mgr = TaskManager(_project_dir())
    try:
        state = TaskState(new_state)
    except ValueError:
        console.print(f"[red]Invalid state[/]: {new_state}")
        console.print(f"Valid states: {[s.value for s in TaskState]}")
        sys.exit(1)
    try:
        if final_state and state == TaskState.REVIEW:
            mgr.set_final_state(task_id, final_state)
        t = mgr.transition(task_id, state)
        console.print(f"[green]Transitioned[/]: {t.id} → {t.state.value}")
        if state == TaskState.PLAN:
            plan_path = mgr.tasks_dir / task_id / "plan.md"
            if plan_path.exists():
                console.print(f"  Plan scaffold: {plan_path}")
        elif state == TaskState.EXECUTE:
            from .worktree import WorktreeManager

            project_dir = _project_dir()
            active = WorktreeManager.load_for_task(project_dir, task_id)
            if active and active.worktree_path:
                console.print(f"  Worktree: {active.worktree_path}")
                console.print(f"  Branch: {active.branch_name}")
                console.print(f"  [dim]cd {active.worktree_path}[/]")
            elif WorktreeManager.is_inside_linked_worktree(project_dir):
                # HATS-060: adopted the caller's linked worktree.
                console.print(f"  Worktree: {project_dir} [dim](adopted — already cwd)[/]")
        elif state == TaskState.DONE:
            console.print("  Worktree merged")
        elif state == TaskState.FAILED:
            console.print("  Worktree discarded")
    except ValueError as e:
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)


@task.command("update")
@click.argument("task_id")
@click.option("--title", default=None, help="New title")
@click.option("--description", "-d", default=None, help="New description")
@click.option(
    "--priority", "-p", default=None, type=click.Choice(["low", "medium", "high"]), help="Priority"
)
@click.option("--resolution", default=None, help="Resolution note (why closed)")
@click.option("--role", default=None, help="Assigned role")
@click.option("--reviewer", default=None, help="Reviewer")
@click.option("--add-tag", multiple=True, help="Add tag")
@click.option("--remove-tag", multiple=True, help="Remove tag")
def task_update(
    task_id: str,
    title: str | None,
    description: str | None,
    priority: str | None,
    resolution: str | None,
    role: str | None,
    reviewer: str | None,
    add_tag: tuple,
    remove_tag: tuple,
):
    """Update task card fields."""
    from .state import TaskManager

    mgr = TaskManager(_project_dir())

    has_changes = any(
        [title, description, priority, resolution, role, reviewer, add_tag, remove_tag]
    )
    if not has_changes:
        console.print(
            "[yellow]No changes specified[/]. Use --title, --priority, --description, etc."
        )
        return

    try:
        t = mgr.update_task(
            task_id,
            title=title,
            description=description,
            priority=priority,
            resolution=resolution,
            role=role,
            reviewer=reviewer,
            add_tags=list(add_tag) if add_tag else None,
            remove_tags=list(remove_tag) if remove_tag else None,
        )
        console.print(f"[green]Updated[/]: {t.id} — {t.title} [{t.priority}]")
    except ValueError as e:
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)


@task.command("log")
@click.argument("task_id")
@click.argument("message")
@click.option("--session", default=None, help="Session ID (defaults to AI_HATS_SESSION_ID)")
def task_log(task_id: str, message: str, session: str | None):
    """Log work progress on a task."""
    from .state import TaskManager

    mgr = TaskManager(_project_dir())
    try:
        t = mgr.log_work(task_id, message, session_id=session or "")
        console.print(f"[green]Logged[/]: {t.id} — {message}")
    except ValueError as e:
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)


@task.command("list")
@click.option("--state", default=None, help="Filter by state")
@click.option("--priority", default=None, help="Filter by priority (low/medium/high)")
@click.option("--all", "-a", "show_all", is_flag=True, help="Include done/failed tasks")
@click.option("--search", "-s", default=None, help="Regex search across id, title, description, tags, parent_task")
def task_list(state: str | None, priority: str | None, show_all: bool, search: str | None):
    """List all task cards."""
    import re as _re

    from rich.table import Table

    from .models import TaskState
    from .state import TaskManager

    STATE_ORDER = {
        TaskState.EXECUTE: 0,
        TaskState.DOCUMENT: 1,
        TaskState.REVIEW: 2,
        TaskState.PLAN: 3,
        TaskState.BRAINSTORM: 4,
        TaskState.BLOCKED: 5,
        TaskState.DONE: 6,
        TaskState.FAILED: 7,
    }
    PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

    mgr = TaskManager(_project_dir())
    filter_state = TaskState(state) if state else None
    tasks = mgr.list_tasks(state=filter_state, priority=priority)

    if not show_all and filter_state is None:
        tasks = [t for t in tasks if t.state not in (TaskState.DONE, TaskState.FAILED)]

    if search:
        try:
            pattern = _re.compile(search, _re.IGNORECASE)
        except _re.error as e:
            console.print(f"[red]Bad regex[/]: {e}")
            sys.exit(1)
        tasks = [
            t for t in tasks
            if pattern.search(
                "\n".join([t.id, t.title, t.description, t.parent_task, *t.tags])
            )
        ]

    if not tasks:
        console.print("[dim]No tasks[/]")
        return

    tasks.sort(key=lambda t: (STATE_ORDER.get(t.state, 99), PRIORITY_ORDER.get(t.priority, 99)))

    table = Table(show_header=True, header_style="bold", padding=(0, 1))
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Pri", no_wrap=True)
    table.add_column("Title")
    table.add_column("Parent", style="dim")

    state_styles = {
        TaskState.EXECUTE: "bold green",
        TaskState.PLAN: "yellow",
        TaskState.BRAINSTORM: "dim",
        TaskState.BLOCKED: "bold red",
        TaskState.DONE: "dim green",
        TaskState.FAILED: "dim red",
    }

    for t in tasks:
        style = state_styles.get(t.state, "")
        table.add_row(
            t.id,
            f"[{style}]{t.state.value}[/{style}]" if style else t.state.value,
            t.priority,
            t.title,
            t.parent_task or "",
        )

    console.print(table)


@task.command("show")
@click.argument("task_id")
def task_show(task_id: str):
    """Show task card details."""
    from .state import TaskManager

    mgr = TaskManager(_project_dir())
    t = mgr.get_task(task_id)
    if t is None:
        console.print(f"[red]Task not found[/]: {task_id}")
        sys.exit(1)
    for k, v in t.to_dict().items():
        if v:
            console.print(f"  {k}: {v}")
    # Show work log nicely
    if t.work_log:
        console.print("\n  [bold]Work Log:[/]")
        for entry in t.work_log:
            console.print(f"    {entry.timestamp} — {entry.message}")


@task.command("sync")
def task_sync():
    """Synchronize backlog.md and STATE.md with task cards."""
    from .state import TaskManager

    mgr = TaskManager(_project_dir())
    count = mgr.sync()
    console.print(f"[green]Synced[/]: {count} tasks")


GIT_INSTALL_URL = "git+ssh://git@github.com/muratovv/ai-hats.git"


def _build_update_cmd() -> list[str]:
    """Build the pip command for updating ai-hats from GitHub."""
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-deps",
        "--no-cache-dir",
        f"ai-hats @ {GIT_INSTALL_URL}",
    ]


# -- update --


def _get_installed_version() -> str:
    """Get the currently installed ai-hats version via subprocess.

    Uses a fresh Python process to avoid import caching.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "from ai_hats import __version__; print(__version__)"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _get_changelog() -> str:
    """Get recent commits from GitHub via shallow clone."""
    import subprocess
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ai-hats-changelog-")
    try:
        result = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "10",
                "--filter=blob:none",
                "--quiet",
                "ssh://git@github.com/muratovv/ai-hats.git",
                tmp,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return ""
        log = subprocess.run(
            ["git", "-C", tmp, "log", "--oneline", "-7"],
            capture_output=True,
            text=True,
        )
        return log.stdout.strip() if log.returncode == 0 else ""
    except Exception:
        return ""
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _snapshot_library() -> dict[str, set[str]]:
    """Snapshot available component names from built-in + global library paths."""
    from .library import LibraryResolver
    from .models import ComponentType

    builtin = Path(__file__).parent / "libraries"
    paths = [p for p in [builtin, Path.home() / ".ai-hats"] if p.is_dir()]
    resolver = LibraryResolver(paths)
    return {ct.value: set(resolver.list_components(ct)) for ct in ComponentType}


def _format_component_diff(
    before: dict[str, set[str]],
    after: dict[str, set[str]],
) -> bool:
    """Print added/removed components. Returns True if any changes found."""
    any_changes = False
    for component_type in ("role", "trait", "rule", "skill"):
        old = before.get(component_type, set())
        new = after.get(component_type, set())
        added = sorted(new - old)
        removed = sorted(old - new)
        if added or removed:
            any_changes = True
            for name in added:
                console.print(f"  [green]+[/] {component_type}: {name}", highlight=False)
            for name in removed:
                console.print(f"  [red]-[/] {component_type}: {name}", highlight=False)
    return any_changes


def _snapshot_composition(asm) -> tuple[set[str], set[str]]:
    """Snapshot current role's rules and skills via composition."""
    from .models import ProfileConfig

    profile = ProfileConfig.load(asm.profile_path)
    if not profile.active_role:
        return set(), set()
    try:
        result = asm.composer.compose(
            profile.active_role,
            overlay=asm._get_overlay(profile.active_role),
        )
        return {r.name for r in result.rules}, {s.name for s in result.skills}
    except Exception:
        return set(), set()


@main.command()
def update():
    """Update ai-hats from GitHub."""
    import subprocess

    from . import __version__ as old_version
    from .models import ProfileConfig

    console.print(f"Current version: [bold]{old_version}[/]")

    # 1. Snapshot before update
    before_lib = _snapshot_library()
    project_dir = _project_dir()
    profile_path = project_dir / "profile.json"
    active_role = None
    before_rules: set[str] = set()
    before_skills: set[str] = set()

    if profile_path.exists() and (project_dir / "ai-hats.yaml").exists():
        profile = ProfileConfig.load(profile_path)
        active_role = profile.active_role or None
        if active_role:
            asm = _assembler(project_dir)
            before_rules, before_skills = _snapshot_composition(asm)

    # 2. Install
    console.print("Updating from GitHub...")
    cmd = _build_update_cmd()
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]Update failed[/]: {result.stderr}")
        return

    # 3. Version diff
    new_version = _get_installed_version()
    if new_version == old_version:
        console.print(f"[green]Already up to date[/] ({old_version})")
    else:
        console.print(f"[green]Updated[/]: {old_version} → [bold]{new_version}[/]")

        changelog = _get_changelog()
        if changelog:
            console.print("\n[bold]Recent changes:[/]")
            for line in changelog.splitlines()[:7]:
                console.print(f"  {line}")

    # 4. Library diff
    after_lib = _snapshot_library()
    console.print("\n[bold]Library:[/]")
    if not _format_component_diff(before_lib, after_lib):
        console.print("  [dim]No changes[/]")

    # 5. Auto-migrate
    console.print("\n[bold]Migration:[/]")
    migrate.invoke(click.Context(migrate))

    # 6. Auto-bump if role active
    if active_role:
        console.print(f"\n[bold]Re-assembling:[/] {active_role}")
        try:
            asm = _assembler(project_dir)
            bump_result = asm.bump()
            if bump_result:
                after_rules = {r.name for r in bump_result.rules}
                after_skills = {s.name for s in bump_result.skills}
                added_r = sorted(after_rules - before_rules)
                removed_r = sorted(before_rules - after_rules)
                added_s = sorted(after_skills - before_skills)
                removed_s = sorted(before_skills - after_skills)
                has_diff = bool(added_r or removed_r or added_s or removed_s)
                if has_diff:
                    for r in added_r:
                        console.print(f"  [green]+[/] rule: {r}", highlight=False)
                    for r in removed_r:
                        console.print(f"  [red]-[/] rule: {r}", highlight=False)
                    for s in added_s:
                        console.print(f"  [green]+[/] skill: {s}", highlight=False)
                    for s in removed_s:
                        console.print(f"  [red]-[/] skill: {s}", highlight=False)
                else:
                    console.print("  [dim]No composition changes[/]")
                if bump_result.errors:
                    for err in bump_result.errors:
                        console.print(f"  [yellow]{err}[/]")
        except Exception as e:
            console.print(f"  [red]Bump failed[/]: {e}")


# -- migrate --


@main.command()
def migrate():
    """Run migrations for ai-hats updates."""
    from .models import ProjectConfig

    project_dir = _project_dir()
    config_path = project_dir / "ai-hats.yaml"

    if not config_path.exists():
        console.print("[yellow]No ai-hats.yaml found — nothing to migrate[/]")
        return

    config = ProjectConfig.from_yaml(config_path)
    current_version = config.schema_version

    # Run migrations in order
    migrations_applied = 0
    # Future: add migration functions here as schema evolves
    # if current_version < 2:
    #     _migrate_v1_to_v2(project_dir)
    #     current_version = 2
    #     migrations_applied += 1

    if migrations_applied > 0:
        config.schema_version = current_version
        config.save(config_path)
        console.print(f"[green]Migrated[/] to schema version {current_version}")
    else:
        console.print(f"[dim]Already at latest schema version ({current_version})[/]")
