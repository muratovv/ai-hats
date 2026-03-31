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


@click.group(invoke_without_command=True)
@click.version_option(version=__version__)
@click.option("--provider", "-p", default=None, help="Provider override (gemini/claude)", is_eager=True)
@click.option("--role", "-r", default=None, help="Role override", is_eager=True)
@click.option("--keep-raw", is_flag=True, default=False, help="Keep raw trace.log after audit", is_eager=True)
@click.pass_context
def main(ctx, provider: str | None, role: str | None, keep_raw: bool):
    """ai-hats — AI agent role composition framework.

    Without a subcommand, launches a wrapped CLI session.
    """
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is None:
        _do_wrap(provider=provider, role=role, keep_raw=keep_raw)


# -- init --

@main.command()
@click.option("--role", default=None, help="Initial role to apply")
@click.option("--provider", default=None, help="Provider (gemini/claude)")
def init(role: str | None, provider: str | None):
    """Initialize project for ai-hats."""
    project_dir = _project_dir()
    asm = _assembler(project_dir)
    asm.init(role=role, provider=provider)
    console.print(f"[green]Initialized[/] ai-hats in {project_dir}")
    if role:
        console.print(f"  Role: [bold]{role}[/]")
    console.print(f"  Provider: [bold]{provider or asm.project_config.provider}[/]")


# -- set --

@main.command("set")
@click.argument("role")
@click.option("--provider", default=None, help="Override provider")
def set_role(role: str, provider: str | None):
    """Apply a role to the project."""
    asm = _assembler()
    result = asm.set_role(role, provider_name=provider)

    if result.errors:
        for err in result.errors:
            console.print(f"  [yellow]Warning[/]: {err}")

    console.print(f"[green]Role set[/]: [bold]{result.name}[/]")
    console.print(f"  Rules: {len(result.rules)}")
    console.print(f"  Skills: {len(result.skills)}")
    console.print(f"  Injections: {len(result.injections)}")


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
            console.print(f"  {component}: {icon}")

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
    from .library import LibraryResolver

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


# -- wrap --

def _do_wrap(
    provider: str | None = None,
    role: str | None = None,
    extra_args: list[str] | None = None,
    keep_raw: bool = False,
):
    """Shared wrap logic — launch a CLI session."""
    from .models import ProfileConfig, ProjectConfig
    from .runtime import WrapRunner

    project_dir = _project_dir()
    profile = ProfileConfig.load(project_dir / "profile.json")
    config = ProjectConfig.from_yaml(project_dir / "ai-hats.yaml")

    effective_provider = provider or profile.provider or config.provider
    if not effective_provider:
        console.print("[red]No provider configured[/]. Use -p or run ai-hats init.")
        sys.exit(1)

    runner = WrapRunner(project_dir)
    exit_code = runner.run(
        effective_provider, role_override=role, extra_args=extra_args, keep_raw=keep_raw,
    )
    sys.exit(exit_code)


@main.command()
@click.option("--provider", "-p", default=None, help="Provider override (gemini/claude)")
@click.option("--role", "-r", default=None, help="Role override")
@click.option("--keep-raw", is_flag=True, default=False, help="Keep raw trace.log after audit")
@click.argument("extra_args", nargs=-1)
def wrap(provider: str | None, role: str | None, keep_raw: bool, extra_args: tuple):
    """Launch a CLI session (same as running ai-hats with no subcommand)."""
    _do_wrap(provider=provider, role=role, extra_args=list(extra_args) or None, keep_raw=keep_raw)


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
@click.argument("task_id", required=False, default=None)
@click.argument("title")
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
def task_list(state: str | None):
    """List all task cards."""
    from .models import TaskState
    from .state import TaskManager
    mgr = TaskManager(_project_dir())
    filter_state = TaskState(state) if state else None
    tasks = mgr.list_tasks(state=filter_state)
    if not tasks:
        console.print("[dim]No tasks[/]")
        return
    for t in tasks:
        priority_tag = f" [{t.priority}]" if t.priority != "medium" else ""
        console.print(f"  [{t.state.value:10}] {t.id}: {t.title}{priority_tag}")


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
             f"ssh://git@github.com/muratovv/ai-hats.git", tmp],
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
