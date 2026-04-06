"""CLI interface — Click-based command-line tool."""

from __future__ import annotations

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


@click.group(
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
    add_trait: tuple, remove_trait: tuple,
    add_rule: tuple, remove_rule: tuple,
    add_skill: tuple, remove_skill: tuple,
    injection_append: str | None,
    show_only: bool, do_reset: bool,
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

    has_changes = any([add_trait, remove_trait, add_rule, remove_rule, add_skill, remove_skill, injection_append])
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
@click.option("--trait", "as_trait", is_flag=True, default=False, help="Analyze as trait instead of role")
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
    table.add_column("Chars", justify="right", style="dim", footer=f"{sum(c.chars for c in breakdown.components):,}")

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
        effective_provider, role_override=role, extra_args=extra_args or None,
    )
    sys.exit(exit_code)


# -- run --

@main.command("run")
@click.argument("role")
@click.option("--ticket", default=None, help="Ticket/task ID for context")
@click.option("--model", default=None, help="Model override")
@click.option("--task", default=None, help="Task description")
@click.option(
    "--isolation", default="discard",
    type=click.Choice(["discard", "squash", "branch"]),
    help="Worktree isolation mode (default: discard)",
)
def run_subagent(role: str, ticket: str | None, model: str | None, task: str | None, isolation: str):
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
    active = WorktreeManager.load_active(project_dir)
    if active is not None:
        console.print(f"[red]Active worktree already exists[/]: {active.branch_name}")
        console.print(f"  Path: {active.worktree_path}")
        console.print("  Run [bold]ai-hats wt merge[/] or [bold]ai-hats wt discard[/] first.")
        sys.exit(1)

    mgr = WorktreeManager(project_dir, branch_name=branch)
    wt_path = mgr.create()
    mgr.save_state()
    console.print(f"[green]Worktree created[/]: {branch}")
    console.print(f"  Path: {wt_path}")
    console.print(f"  [dim]cd {wt_path}[/]")


@wt.command("merge")
@click.option("--no-squash", is_flag=True, default=False, help="Regular merge instead of squash")
def wt_merge(no_squash: bool):
    """Merge worktree changes back and clean up."""
    from .worktree import WorktreeManager

    mgr = WorktreeManager.load_active(_project_dir())
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        sys.exit(1)

    branch = mgr.branch_name
    mgr.merge(squash=not no_squash)
    console.print(f"[green]Merged[/]: {branch}")


@wt.command("discard")
def wt_discard():
    """Discard worktree changes and clean up."""
    from .worktree import WorktreeManager

    mgr = WorktreeManager.load_active(_project_dir())
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        sys.exit(1)

    branch = mgr.branch_name
    mgr.discard()
    console.print(f"[green]Discarded[/]: {branch}")


@wt.command("list")
def wt_list():
    """List all git worktrees."""
    from .worktree import WorktreeManager

    project_dir = _project_dir()
    worktrees = WorktreeManager.list_worktrees(project_dir)
    active = WorktreeManager.load_active(project_dir)
    active_branch = active.branch_name if active else None

    if not worktrees:
        console.print("[dim]No worktrees[/]")
        return

    for w in worktrees:
        branch = w.get("branch", "?")
        path = w.get("path", "?")
        marker = " [green]← active[/]" if branch == active_branch else ""
        console.print(f"  {branch}: {path}{marker}")


@wt.command("status")
def wt_status():
    """Show active worktree info."""
    from .worktree import WorktreeManager

    mgr = WorktreeManager.load_active(_project_dir())
    if mgr is None:
        console.print("[dim]No active worktree[/]")
        return

    console.print(f"  Branch: [bold]{mgr.branch_name}[/]")
    console.print(f"  Path: {mgr.worktree_path}")


# -- judge --

@main.command()
@click.option("--session", default=None, help="Session ID to judge")
@click.option("--last", "last_n", default=None, type=int, help="Judge last N sessions")
def judge(session: str | None, last_n: int | None):
    """Evaluate session quality."""
    from .feedback import JudgeRunner
    runner = JudgeRunner(_project_dir())

    if session:
        verdict_path = runner.judge_session(session)
        console.print(f"[green]Verdict[/]: {verdict_path}")
    elif last_n:
        verdicts = runner.judge_last(last_n)
        for v in verdicts:
            console.print(f"[green]Verdict[/]: {v}")
    else:
        verdicts = runner.judge_last(1)
        for v in verdicts:
            console.print(f"[green]Verdict[/]: {v}")


# -- retro --

@main.command()
@click.option("--session", default=None, help="Session ID for retrospective")
def retro(session: str | None):
    """Generate retrospective for a session."""
    from .feedback import RetroGenerator
    gen = RetroGenerator(_project_dir())
    retro_path = gen.generate(session_id=session)
    console.print(f"[green]Retrospective[/]: {retro_path}")


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
def task_create(task_id: str | None, title: str, description: str, priority: str, role: str, reviewer: str, tag: tuple):
    """Create a new task card. ID is auto-generated if omitted."""
    from .state import TaskManager
    mgr = TaskManager(_project_dir())
    if task_id is None:
        task_id = mgr.next_id()
    t = mgr.create_task(
        task_id, title,
        description=description, priority=priority,
        role=role, reviewer=reviewer, tags=list(tag),
    )
    console.print(f"[green]Created[/]: {t.id} — {t.title} [{t.state.value}] ({t.priority})")


@task.command("transition")
@click.argument("task_id")
@click.argument("new_state")
@click.option("--final-state", default=None, help="Final accomplished state (for review transition)")
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
            active = WorktreeManager.load_active(_project_dir())
            if active and active.worktree_path:
                console.print(f"  Worktree: {active.worktree_path}")
                console.print(f"  Branch: {active.branch_name}")
                console.print(f"  [dim]cd {active.worktree_path}[/]")
        elif state == TaskState.DONE:
            console.print("  Worktree merged")
        elif state == TaskState.FAILED:
            console.print("  Worktree discarded")
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
def task_list(state: str | None, priority: str | None, show_all: bool):
    """List all task cards."""
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

    if not tasks:
        console.print("[dim]No tasks[/]")
        return

    tasks.sort(key=lambda t: (STATE_ORDER.get(t.state, 99), PRIORITY_ORDER.get(t.priority, 99)))

    table = Table(show_header=True, header_style="bold", padding=(0, 1))
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Pri", no_wrap=True)
    table.add_column("Title")

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
        sys.executable, "-m", "pip", "install",
        "--force-reinstall", "--no-deps", "--no-cache-dir",
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
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _get_changelog() -> str:
    """Get recent commits from GitHub via shallow clone."""
    import subprocess
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ai-hats-changelog-")
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "10", "--filter=blob:none", "--quiet",
             "ssh://git@github.com/muratovv/ai-hats.git", tmp],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return ""
        log = subprocess.run(
            ["git", "-C", tmp, "log", "--oneline", "-7"],
            capture_output=True, text=True,
        )
        return log.stdout.strip() if log.returncode == 0 else ""
    except Exception:
        return ""
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


@main.command()
def update():
    """Update ai-hats from GitHub."""
    import subprocess

    from . import __version__ as old_version
    console.print(f"Current version: [bold]{old_version}[/]")
    console.print("Updating from GitHub...")

    cmd = _build_update_cmd()
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]Update failed[/]: {result.stderr}")
        return

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
            console.print()

    # Auto-migrate
    console.print("Running migration...")
    migrate.invoke(click.Context(migrate))




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
