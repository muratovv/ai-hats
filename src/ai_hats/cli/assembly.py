"""Role-assembly commands: init, set, customize, status, bump, rollback, clean."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import click
from rich.tree import Tree

from ..providers import PROVIDERS
from ._helpers import _assembler, _project_dir, console


def _stdin_is_tty() -> bool:
    """Indirection over ``sys.stdin.isatty()`` so tests can monkeypatch it."""
    return sys.stdin.isatty()


def _detect_provider_default() -> str | None:
    """Smart default for provider — based on existence of ~/.<provider>.

    Returns the first provider whose home-config directory exists. Order
    matches PROVIDERS dict iteration (deterministic via insertion order).
    Returns ``None`` if no provider home directory is present.
    """
    home = Path.home()
    for name in PROVIDERS:
        if (home / f".{name}").is_dir():
            return name
    return None


def _wizard_provider_prompt(default: str | None) -> str:
    """Interactive numbered menu for provider selection."""
    names = list(PROVIDERS.keys())
    console.print("[bold]Choose provider:[/]")
    for idx, name in enumerate(names, start=1):
        marker = f" [dim](recommended — found ~/.{name})[/]" if name == default else ""
        console.print(f"  {idx}) {name}{marker}")
    default_idx = names.index(default) + 1 if default else None
    while True:
        raw = click.prompt(
            f"Provider [1-{len(names)}]",
            default=str(default_idx) if default_idx else None,
            show_default=bool(default_idx),
        )
        if raw in names:
            return raw
        try:
            idx = int(raw)
            if 1 <= idx <= len(names):
                return names[idx - 1]
        except ValueError:
            pass
        console.print(f"[red]Invalid choice[/]: {raw!r}. Enter 1..{len(names)} or a provider name.")


def _run_self_update() -> bool:
    """Run `pip install -U` for ai-hats inline. Returns True on success.

    Used in the wizard bootstrap path to guarantee that newly-onboarded
    users start with the latest framework version. Skipped in flag-only
    (CI) mode and behind ``--no-update`` for tests / offline use.
    """
    import subprocess

    from .maintenance import _build_update_cmd

    console.print("[cyan]→ Updating ai-hats from GitHub …[/]")
    cmd = _build_update_cmd()
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Don't abort init on update failure — surface and continue with
        # the currently-installed version. Common cause: offline / no
        # network access on first-time setup.
        msg = (result.stderr or result.stdout or "").strip().splitlines()
        tail = msg[-1] if msg else "see logs"
        console.print(f"[yellow]Update skipped[/]: {tail}")
        return False
    console.print("[green]✓[/] ai-hats updated")
    return True


def _launch_wizard_session() -> None:
    """Replace the current process with `ai-hats execute --role initial-wizard`.

    Uses ``os.execvp`` so the interactive provider CLI takes over the
    terminal cleanly — same handoff pattern as ``exec_claude_with_retro``.
    """
    ai_hats_bin = shutil.which("ai-hats")
    if not ai_hats_bin:
        console.print(
            "[yellow]ai-hats binary not in PATH — cannot auto-launch wizard.[/]\n"
            "Run manually:  ai-hats execute --role initial-wizard --prompt initial-wizard",
        )
        return
    console.print("[cyan]→ Launching initial-wizard session …[/]")
    os.execvp(
        ai_hats_bin,
        [ai_hats_bin, "execute", "--role", "initial-wizard", "--prompt", "initial-wizard"],
    )


@click.command()
@click.option("--provider", "-p", default=None, help="Provider (gemini/claude)")
@click.option("--role", "-r", default=None, help="Role to apply after init")
@click.option(
    "--task-prefix", "task_prefix", default=None,
    help="Task-id prefix for `ai-hats task create` (e.g. ACME). "
    "Default: TASK for new projects; auto-detected for legacy repos.",
)
@click.option(
    "--no-wizard",
    is_flag=True,
    default=False,
    help="Skip the interactive wizard (CI / scripted mode).",
)
@click.option(
    "--no-update",
    is_flag=True,
    default=False,
    help="Skip the `self update` step that wizard-path init normally runs.",
)
def init(
    provider: str | None,
    role: str | None,
    task_prefix: str | None,
    no_wizard: bool,
    no_update: bool,
):
    """Initialize ai-hats in the current directory.

    Default (TTY, no -p/-r flags) → launches an interactive wizard:
    asks for provider (smart default by ~/.<provider> presence), writes a
    minimal ai-hats.yaml, then hands off to the `initial-wizard` role
    session for stack-detection, role selection, customization and
    feedback-policy setup.

    Flag-only path (both --provider and --role provided, or --no-wizard,
    or non-TTY stdin) preserves the original non-interactive behavior
    for CI and scripted invocations.
    """
    project_dir = _project_dir()
    already = (project_dir / "ai-hats.yaml").exists()

    # Wizard runs only when stdin is a TTY and the user did NOT supply
    # both -p and -r (which we treat as a fully-scripted invocation).
    use_wizard = (
        not no_wizard
        and _stdin_is_tty()
        and not (provider and role)
    )

    if not use_wizard and provider is None and role is None and not no_wizard:
        console.print(
            "[red]No TTY and no flags[/]: cannot run interactive wizard.\n"
            "Pass --provider/-p (and optionally --role/-r), or run with "
            "--no-wizard to bootstrap a minimal config.",
        )
        raise SystemExit(2)

    # Wizard path step 0: ensure the framework itself is up to date. The
    # subsequent `_launch_wizard_session()` os.execvp's into the freshly
    # installed binary, so the wizard session uses the new code.
    if use_wizard and not no_update:
        _run_self_update()

    if use_wizard and provider is None:
        default = _detect_provider_default()
        provider = _wizard_provider_prompt(default)

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

    # Hand off to the wizard role for the remaining configuration steps
    # (stack detection, role selection, customization, feedback policy).
    # Skipped when -r was given (role already chosen), --no-wizard, or non-TTY.
    if use_wizard and not role:
        _launch_wizard_session()


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
        console.print("[red]No ai-hats.yaml found[/]. Run: ai-hats config set -r <role> -p <provider>")
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


