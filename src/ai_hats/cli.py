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


@click.group()
@click.version_option(version=__version__)
def main():
    """ai-hats — AI agent role composition framework."""
    pass


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


# -- wrap --

@main.group()
def wrap():
    """Launch a CLI session through ai-hats wrapper."""
    pass


@wrap.command("gemini")
@click.option("--role", default=None, help="Role override")
@click.argument("extra_args", nargs=-1)
def wrap_gemini(role: str | None, extra_args: tuple):
    """Launch Gemini CLI through ai-hats wrapper."""
    from .runtime import WrapRunner
    runner = WrapRunner(_project_dir())
    exit_code = runner.run("gemini", role_override=role, extra_args=list(extra_args) or None)
    sys.exit(exit_code)


@wrap.command("claude")
@click.option("--role", default=None, help="Role override")
@click.argument("extra_args", nargs=-1)
def wrap_claude(role: str | None, extra_args: tuple):
    """Launch Claude CLI through ai-hats wrapper."""
    from .runtime import WrapRunner
    runner = WrapRunner(_project_dir())
    exit_code = runner.run("claude", role_override=role, extra_args=list(extra_args) or None)
    sys.exit(exit_code)


# -- run --

@main.command("run")
@click.argument("role")
@click.option("--ticket", default=None, help="Ticket/task ID for context")
@click.option("--model", default=None, help="Model override")
@click.option("--task", default=None, help="Task description")
def run_subagent(role: str, ticket: str | None, model: str | None, task: str | None):
    """Run a sub-agent with the given role."""
    from .runtime import SubAgentRunner
    runner = SubAgentRunner(_project_dir())
    session = runner.run(
        role_name=role,
        task=task or "",
        ticket_id=ticket or "",
        model=model or "",
    )
    console.print(f"[green]Sub-agent completed[/]: {session.session_id}")
    console.print(f"  Session dir: {session.session_dir}")


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


# -- self-update --

@main.command("self-update")
def self_update():
    """Update ai-hats framework."""
    import subprocess
    console.print("Updating ai-hats...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "ai-hats"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print("[green]Updated[/]")
        # Auto-migrate
        console.print("Running migration...")
        migrate.invoke(click.Context(migrate))
    else:
        console.print(f"[red]Update failed[/]: {result.stderr}")


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
