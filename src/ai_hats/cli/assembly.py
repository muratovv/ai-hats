"""Role-assembly commands: init, set, customize, status, bump, clean.

HATS-407: ``rollback`` was removed — ``config set`` is yaml-only, ``init`` /
``bump`` only touch git-tracked scaffold files (``./CLAUDE.md`` / ``.gitignore``)
and the gitignored canonical aggregator, so ``git checkout`` is the recovery
path documented in the v0.7 CHANGELOG (HATS-409).
"""

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

    Wraps the pip subprocess in a Rich spinner so users on slow links
    see continuous progress instead of a silent terminal.
    """
    import subprocess

    from .maintenance import _build_update_cmd

    cmd = _build_update_cmd()
    with console.status(
        "[cyan]Downloading ai-hats from GitHub …[/] "
        "[dim](first run can take a minute on slow links)[/]",
        spinner="dots",
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Don't abort init on update failure — surface and continue with
        # the currently-installed version. Common cause: offline / no
        # network access on first-time setup.
        msg = (result.stderr or result.stdout or "").strip().splitlines()
        tail = msg[-1] if msg else "see logs"
        console.print(f"[yellow]Update skipped[/]: {tail}")
        return False
    console.print("[green]✓[/] ai-hats updated from GitHub")
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
    "--ai-hats-dir",
    "ai_hats_dir",
    default=None,
    help="Custom framework directory (default: .agent/ai-hats). "
    "Cannot be changed after first init without manual relocation.",
)
@click.option(
    "--venv",
    "venv_path",
    default=None,
    help="Point ai-hats at an existing venv instead of the managed "
    "default <ai-hats-dir>/.venv. Path can be relative or absolute.",
)
@click.option(
    "--no-manage-gitignore",
    "no_manage_gitignore",
    is_flag=True,
    default=False,
    help="Do not auto-add ai-hats entries to .gitignore.",
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
    ai_hats_dir: str | None,
    venv_path: str | None,
    no_manage_gitignore: bool,
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

    # HATS-366: the wizard no longer asks for ai_hats_dir / venv / gitignore
    # at the CLI prompt — initial-wizard handles those inside the LLM session
    # via `ai-hats config set ...`. The corresponding `self init` flags
    # (--ai-hats-dir, --venv, --no-manage-gitignore) remain for scripted use.
    manage_gitignore: bool | None = False if no_manage_gitignore else None

    asm = _assembler(project_dir)
    try:
        asm.init(
            provider=provider,
            role=role,
            task_prefix=task_prefix,
            ai_hats_dir=ai_hats_dir,
            venv_path=venv_path,
            manage_gitignore=manage_gitignore,
        )
    except ValueError as err:
        console.print(f"[red]Error[/]: {err}")
        raise SystemExit(1)

    label = "Re-initialized" if already else "Initialized"
    console.print(f"[green]{label}[/] ai-hats in {project_dir}")

    if role:
        console.print(f"  Default role: [bold]{role}[/]")
    console.print(f"  Provider: [bold]{provider or asm.project_config.provider}[/]")
    if task_prefix:
        console.print(f"  Task prefix: [bold]{asm.project_config.task_prefix}[/]")
    if ai_hats_dir:
        console.print(f"  ai-hats dir: [bold]{asm.project_config.ai_hats_dir}[/]")
    if venv_path:
        console.print(f"  Venv path: [bold]{asm.project_config.venv_path}[/]")
    if manage_gitignore is False:
        console.print("  manage_gitignore: [bold]disabled[/]")

    # HATS-407: surface the Fork B trade — bare ``claude`` in project_dir
    # loads only user-rules. Suppressed when handing off to the wizard
    # (the wizard role explains this in its own copy).
    if not (use_wizard and not role):
        console.print(
            "  [dim]💡 Direct `claude` reads only user-rules. "
            "Run `ai-hats execute [-r ROLE]` for role-loaded sessions.[/]"
        )

    # Hand off to the wizard role for the remaining configuration steps
    # (stack detection, role selection, customization, feedback policy).
    # Skipped when -r was given (role already chosen), --no-wizard, or non-TTY.
    if use_wizard and not role:
        _launch_wizard_session()


@click.command("set")
@click.option("--provider", "-p", default=None, help="Provider (gemini/claude)")
@click.option("--role", "-r", default=None, help="Role to apply")
@click.option(
    "--task-prefix",
    "task_prefix",
    default=None,
    help="Task-id prefix for `ai-hats task create` (e.g. ACME). Overwrites existing.",
)
@click.option(
    "--venv",
    "venv_path",
    default=None,
    help="Point ai-hats at an existing venv (relative or absolute path). "
    "Pair with --no-venv to reset to the managed default.",
)
@click.option(
    "--no-venv",
    "no_venv",
    is_flag=True,
    default=False,
    help="Reset venv_path to the managed default (<ai_hats_dir>/.venv).",
)
@click.option(
    "--manage-gitignore/--no-manage-gitignore",
    "manage_gitignore",
    default=None,
    help="Toggle whether ai-hats auto-manages the project .gitignore entry.",
)
@click.option(
    "--ai-hats-dir",
    "ai_hats_dir",
    default=None,
    help="Relocate the framework directory. Moves library/, tracker/, "
    "sessions/, STATE.md to the new path; updates yaml and .gitignore; "
    "deletes managed venv (recreated on next session).",
)
def set_role(
    provider: str | None,
    role: str | None,
    task_prefix: str | None,
    venv_path: str | None,
    no_venv: bool,
    manage_gitignore: bool | None,
    ai_hats_dir: str | None,
):
    """Configure project: provider, role, prefix, venv, gitignore, framework dir."""
    from ..models import ProjectConfig

    if venv_path is not None and no_venv:
        console.print("[red]Conflict[/]: pass either --venv PATH or --no-venv, not both.")
        raise SystemExit(1)

    any_change = (
        provider or role or task_prefix is not None
        or venv_path is not None or no_venv
        or manage_gitignore is not None
        or ai_hats_dir is not None
    )
    if not any_change:
        console.print(
            "[red]Specify at least one of[/]: --provider/-p, --role/-r, "
            "--task-prefix, --venv/--no-venv, --manage-gitignore/--no-manage-gitignore, "
            "--ai-hats-dir."
        )
        raise SystemExit(1)

    project_dir = _project_dir()
    asm = _assembler(project_dir)

    # Auto-init if project not yet initialized
    if not (project_dir / "ai-hats.yaml").exists():
        try:
            asm.init(provider=provider, task_prefix=task_prefix)
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

    # task-prefix is set independently of role/provider, and overwrites
    # any existing value (unlike `self init` which refuses conflicts).
    if task_prefix is not None and (project_dir / "ai-hats.yaml").exists():
        try:
            validated = ProjectConfig.validate_task_prefix(task_prefix)
        except ValueError as err:
            console.print(f"[red]Error[/]: {err}")
            raise SystemExit(1)
        asm.project_config.task_prefix = validated
        asm.project_config.save(asm.config_path)
        console.print(f"[green]Task prefix set[/]: [bold]{validated}[/]")

    # --venv / --no-venv — pure yaml field, no filesystem state.
    if venv_path is not None or no_venv:
        from ..paths import normalize_venv_path

        new_venv: str | None = None
        if venv_path is not None:
            try:
                new_venv = normalize_venv_path(venv_path)
            except ValueError as err:
                console.print(f"[red]Error[/]: {err}")
                raise SystemExit(1)
        # else: --no-venv → keep new_venv as None
        if asm.project_config.venv_path == new_venv:
            console.print("[dim]venv_path unchanged[/]")
        else:
            asm.project_config.venv_path = new_venv
            asm.project_config.save(asm.config_path)
            label = new_venv if new_venv is not None else "managed (default)"
            console.print(f"[green]Updated[/]: venv_path = [bold]{label}[/]")

    # --manage-gitignore / --no-manage-gitignore — pure yaml toggle.
    if manage_gitignore is not None:
        if asm.project_config.manage_gitignore == manage_gitignore:
            console.print("[dim]manage_gitignore unchanged[/]")
        else:
            asm.project_config.manage_gitignore = manage_gitignore
            asm.project_config.save(asm.config_path)
            console.print(
                f"[green]Updated[/]: manage_gitignore = [bold]{manage_gitignore}[/]"
            )

    # --ai-hats-dir — relocate framework directory (heavy operation).
    if ai_hats_dir is not None:
        try:
            reloc = asm.relocate(ai_hats_dir)
        except ValueError as err:
            console.print(f"[red]Error[/]: {err}")
            raise SystemExit(1)
        if not reloc.changed:
            console.print("[dim]ai_hats_dir unchanged[/]")
        else:
            console.print(
                f"[green]Relocated[/]: ai_hats_dir = [bold]{reloc.new}[/] "
                f"(was {reloc.old})"
            )
            if reloc.moved:
                console.print(f"  moved: {', '.join(reloc.moved)}")
            if reloc.venv_removed:
                console.print(
                    "  [yellow]managed venv removed[/] — will be recreated "
                    "on next session"
                )
            if reloc.gitignore_updated:
                console.print("  .gitignore entry updated")
            elif not asm.project_config.manage_gitignore:
                console.print(
                    "  [yellow]warning[/]: .gitignore not auto-managed — "
                    "update it manually"
                )

    if role:
        # HATS-407: yaml-only update — composition validates but does not
        # materialize. ``active_role`` stays untouched (it's a runtime
        # cache, written by ``runtime._launch_session`` at session start).
        try:
            result = asm.set_default_role(role, provider_name=provider)
        except ValueError as err:
            console.print(f"[red]Error[/]: {err}")
            raise SystemExit(1)
        if result.errors:
            for err in result.errors:
                console.print(f"  [yellow]Warning[/]: {err}")
        console.print(f"[green]Default role[/]: [bold]{result.name}[/]")
        console.print(f"  Rules: {len(result.rules)}")
        console.print(f"  Skills: {len(result.skills)}")
        console.print(f"  Injections: {len(result.injections)}")
        console.print(
            "  [dim]💡 Composed per-session. Direct `claude` reads only "
            "user-rules; use `ai-hats execute` for role-loaded sessions.[/]"
        )

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
@click.option(
    "--migrate-force",
    is_flag=True,
    help="Bypass v0.6 → v0.7 user-edit refusal (logs WARN per overwritten file).",
)
@click.option(
    "--check-branches",
    is_flag=True,
    help="Warn if local branches modify any v0.7-migration path slated for deletion.",
)
def bump(migrate_force: bool, check_branches: bool):
    """Refresh migrations, scaffold, canonical aggregator, and git hooks.

    HATS-407: no longer re-applies the role — per-session compose
    (HATS-294) handles framework content in memory. ``bump`` is now a
    migration / refresh command for on-disk artefacts.

    HATS-415: the v0.6 → v0.7 migration runs inline. Safe-to-delete v0.6
    files (bytes match composition baseline) are swept transparently;
    user-edited files refuse with per-file guidance pointing at the v0.7
    home. Use ``--migrate-force`` to overwrite user edits (one stderr
    WARN per file). ``--check-branches`` surfaces a warning when local
    branches modify the paths slated for deletion. No auto-commit —
    review with ``git status`` and commit at leisure.
    """
    from ..assembler import AssemblyError

    asm = _assembler()
    try:
        result = asm.bump(
            force_v07_migration=migrate_force,
            check_v07_branches=check_branches,
        )
    except AssemblyError as e:
        # HATS-415: render the user-edits refusal message via Rich so the
        # per-file guidance markup (`[bold]…[/]`, `[yellow]…[/]`) inside
        # ``render_user_edits_refusal`` displays cleanly. ``console.print``
        # auto-renders newline-separated Rich markup.
        console.print(f"[red]Bump refused[/]:\n{e}")
        sys.exit(1)
    if result is None:
        console.print("[green]Bumped[/]: migrations + scaffold + canonical refreshed")
    else:
        console.print(f"[green]Bumped[/]: {result.name} (hooks re-installed)")
    console.print(
        "  [dim]💡 Direct `claude` reads only user-rules. "
        "Run `ai-hats execute [-r ROLE]` for role-loaded sessions.[/]"
    )


@click.command()
def clean():
    """Clean active directories."""
    asm = _assembler()
    asm.clean()
    console.print("[green]Cleaned[/] .agent/ directories")


