"""Role-assembly commands: init, set, customize, status, bump.

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


def _detected_providers() -> list[str]:
    """Providers whose home-config directory (``~/.<provider>``) exists.

    Returns EVERY match in PROVIDERS insertion order (deterministic), not
    just the first — the wizard marks each as ``detected`` and refuses to
    silently prefer one when several are present (HATS-613). Empty list
    when no provider home directory is found.
    """
    home = Path.home()
    return [name for name in PROVIDERS if (home / f".{name}").is_dir()]


def _wizard_provider_prompt(detected: list[str]) -> str:
    """Interactive numbered menu for provider selection.

    Every provider whose ``~/.<name>`` config dir exists is marked
    ``detected``. A click default is pre-selected ONLY when exactly one
    provider is detected — when zero or several are present the choice is
    ambiguous, so the user picks explicitly rather than silently inheriting
    the dict-first provider (HATS-613).
    """
    names = list(PROVIDERS.keys())
    console.print("[bold]Choose provider:[/]")
    for idx, name in enumerate(names, start=1):
        marker = f" [dim](detected — found ~/.{name})[/]" if name in detected else ""
        console.print(f"  {idx}) {name}{marker}")
    # Pre-select a default only when detection is unambiguous (exactly one).
    default_name = detected[0] if len(detected) == 1 else None
    default_idx = names.index(default_name) + 1 if default_name else None
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

    # HATS-549: pre-bump snapshot for re-init paths (non-greenfield).
    # Greenfield init has nothing to back up — the project tree is
    # empty from ai-hats's POV. Re-init reruns the v07 migration +
    # registry, both of which can mutate user-managed state.
    init_backup_path = None
    if already:
        from ..migration_backup import BackupError, snapshot_pre_bump

        try:
            init_backup_path = snapshot_pre_bump(project_dir, label="init")
        except BackupError as be:
            console.print(f"[red]Pre-init backup failed[/]: {be}")
            raise SystemExit(1)

    # Wizard runs only when stdin is a TTY and the user did NOT supply
    # both -p and -r (which we treat as a fully-scripted invocation).
    use_wizard = (
        not no_wizard
        and _stdin_is_tty()
        and not (provider and role)
    )

    # HATS-470 review A2: when the project is already initialized AND no
    # provider/role flags were passed, treat this as "re-apply config"
    # (the replacement for the removed `self bump` CLI). Skip the wizard
    # and let the post-init bump path handle the refresh — never raise
    # the no-TTY-no-flags error in this case.
    if already and provider is None and role is None:
        use_wizard = False
    elif not use_wizard and provider is None and role is None and not no_wizard:
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
        detected = _detected_providers()
        provider = _wizard_provider_prompt(detected)

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

    # HATS-549 Phase 3: end-of-init smoke-assert. Mirrors do_bump's
    # final step — every hook command path in .claude/settings.json
    # must resolve. Only fires on re-init (greenfield has no
    # backup_path AND no pre-existing settings.json to break).
    if already:
        from ..assembler import AssemblyError as _AssemblyError
        from ..migration_assert import assert_runtime_hooks_resolve

        try:
            assert_runtime_hooks_resolve(
                project_dir, backup_path=init_backup_path,
            )
        except _AssemblyError as e:
            console.print(f"[red]Init refused[/]:\n{e}")
            raise SystemExit(1)

    # HATS-469 R6: auto-bump block removed. ``Assembler.init`` itself now
    # calls ``_refresh(install_time=True)`` (which runs migrations + heal
    # + hook install) and ``_run_v07_migration`` on re-init. The only
    # remaining init-time chores not covered by that refresh are the
    # state-condition diagnostics (orphan warning, empty .agent/ note)
    # and the trash-bin summary banner. ``TrashFullError`` from inside
    # ``_refresh`` already propagates up via ``asm.init`` above — no
    # re-raise needed here. Wizard path still skips (the wizard role
    # handles its own session-bootstrap surface).
    if already and not use_wizard:
        from ..safe_delete import session_summary as _trash_summary

        # HATS-469 R3: re-init is user-initiated → diagnostics OK
        # (set_role / runtime path stays silent).
        asm._run_diagnostics()
        banner = _trash_summary()
        if banner:
            console.print(f"  [dim]{banner}[/]")

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


@click.command("sync-hooks")
def sync_hooks():
    """Re-materialize ai-hats-managed git hooks if they have drifted (HATS-593).

    Cheap, idempotent no-op when the hooks already match the composed source.
    Invoked by the post-merge / post-checkout git hooks and at session start so
    ``.githooks/`` never goes stale after a merge / pull / checkout. Fail-open:
    any error is surfaced loudly but never aborts the calling git operation.
    """
    project_dir = _project_dir()
    try:
        res = _assembler(project_dir).sync_hooks()
    except Exception as e:  # noqa: BLE001 — fail-open by design (HATS-593)
        console.print(
            "[yellow]ai-hats: git hooks may be stale[/] "
            f"(sync failed: {e}) — run [bold]ai-hats self init[/]",
        )
        return
    if res.status == "synced":
        console.print("[green]ai-hats:[/] git hooks re-materialized (were drifted)")
    elif res.status == "in-sync":
        console.print("[dim]ai-hats: git hooks in sync[/]")
    elif res.status == "version-skew":
        console.print(
            "[yellow]ai-hats: git hooks are stale but the installed binary is "
            "behind upstream[/] — not healing from an old version. "
            "Run [bold]ai-hats self update[/] then [bold]ai-hats self init[/].",
        )
    else:
        console.print(f"[dim]ai-hats: hook sync skipped[/] ({res.detail})")


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


def _apply_overlay_edits(
    overlay,
    *,
    add_trait: tuple,
    remove_trait: tuple,
    add_rule: tuple,
    remove_rule: tuple,
    add_skill: tuple,
    remove_skill: tuple,
    injection_append: str | None,
) -> None:
    """In-place edit of ``OverlayConfig`` from CLI flags.

    Convention: opposite-side undo (e.g., adding a trait that was previously
    in ``remove_traits`` removes it from there) makes typo correction one
    command. To express a deliberate reorder ("move X to the layer's tail"),
    edit the yaml file directly so that both ``add: [X]`` and ``remove: [X]``
    coexist in the same overlay — composer's sequential apply honours this.
    """
    for t in add_trait:
        if t not in overlay.add_traits:
            overlay.add_traits.append(t)
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


def _print_overlay(layer_label: str, role: str, overlay) -> None:
    """Render an overlay for ``--show`` output, with a layer banner."""
    import yaml as _yaml

    if overlay.is_empty:
        console.print(f"[dim]No {layer_label} customizations for {role}[/]")
        return
    console.print(f"[bold]{role}[/] ({layer_label}) customizations:")
    console.print(_yaml.dump(overlay.to_dict(), default_flow_style=False).rstrip())


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
@click.option(
    "--global",
    "is_global",
    is_flag=True,
    help="Target the user-level customizations (~/.ai-hats/customizations.yaml) "
    "instead of the project. Symmetric in syntax; writes go to your home so they "
    "apply to every project you open (HATS-421).",
)
@click.option(
    "--project",
    "is_project",
    is_flag=True,
    help="Target the project layer explicitly (default; mutually exclusive with --global).",
)
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
    is_global: bool,
    is_project: bool,
):
    """Customize a role — add/remove traits, rules, skills (project or user-level).

    Two layers, symmetric syntax:

    \b
      ai-hats config customize <role> --add-trait T            # project (default)
      ai-hats config customize <role> --add-trait T --global   # user-wide

    Inspection:

    \b
      ai-hats config customize <role> --show                   # both layers
      ai-hats config customize <role> --show --global          # only global
      ai-hats config customize <role> --show --project         # only project

    Reset:

    \b
      ai-hats config customize <role> --reset                  # clear project
      ai-hats config customize <role> --reset --global         # clear global

    Compose order is built-in → global → project, so project edits override
    user-wide defaults for the current project (HATS-421).
    """
    from ..models import OverlayConfig, ProjectConfig, UserConfig

    if is_global and is_project:
        raise click.UsageError("--global and --project are mutually exclusive")

    project_dir = _project_dir()
    project_path = project_dir / "ai-hats.yaml"
    user_path = UserConfig.default_path()

    # ----- SHOW mode -----
    if show_only:
        # `--show` alone shows BOTH layers so the user can see what's at play.
        # `--show --global` / `--show --project` narrow to a single layer.
        if is_global:
            user_cfg = UserConfig.from_yaml(user_path)
            _print_overlay("global", role, user_cfg.customizations.get(role, OverlayConfig()))
            return
        if is_project:
            if not project_path.exists():
                console.print("[red]No ai-hats.yaml found[/].")
                raise SystemExit(1)
            proj_cfg = ProjectConfig.from_yaml(project_path)
            _print_overlay("project", role, proj_cfg.customizations.get(role, OverlayConfig()))
            return
        # No layer flag → render both.
        user_cfg = UserConfig.from_yaml(user_path)
        _print_overlay("global", role, user_cfg.customizations.get(role, OverlayConfig()))
        if project_path.exists():
            proj_cfg = ProjectConfig.from_yaml(project_path)
            _print_overlay(
                "project", role, proj_cfg.customizations.get(role, OverlayConfig())
            )
        else:
            console.print("[dim]No project ai-hats.yaml — only the global layer is in effect.[/]")
        return

    # ----- RESET mode -----
    if do_reset:
        if is_global:
            user_cfg = UserConfig.from_yaml(user_path)
            user_cfg.customizations.pop(role, None)
            user_cfg.save(user_path)
            console.print(
                f"[green]Reset[/] (global) customizations for [bold]{role}[/] "
                f"([dim]{user_path}[/])"
            )
            return
        if not project_path.exists():
            console.print("[red]No ai-hats.yaml found[/].")
            raise SystemExit(1)
        proj_cfg = ProjectConfig.from_yaml(project_path)
        proj_cfg.customizations.pop(role, None)
        proj_cfg.save(project_path)
        console.print(f"[green]Reset[/] (project) customizations for [bold]{role}[/]")
        return

    # ----- WRITE mode -----
    has_changes = any(
        [add_trait, remove_trait, add_rule, remove_rule, add_skill, remove_skill, injection_append]
    )
    if not has_changes:
        console.print("[yellow]No changes specified[/]. Use --add-trait, --remove-trait, etc.")
        return

    if is_global:
        user_cfg = UserConfig.from_yaml(user_path)
        overlay = user_cfg.customizations.get(role, OverlayConfig())
        _apply_overlay_edits(
            overlay,
            add_trait=add_trait,
            remove_trait=remove_trait,
            add_rule=add_rule,
            remove_rule=remove_rule,
            add_skill=add_skill,
            remove_skill=remove_skill,
            injection_append=injection_append,
        )
        user_cfg.customizations[role] = overlay
        user_cfg.save(user_path)
        console.print(
            f"[green]Updated[/] (global) customizations for [bold]{role}[/] "
            f"([dim]{user_path}[/])"
        )
        _print_overlay("global", role, overlay)
        return

    # Default: project layer.
    if not project_path.exists():
        console.print(
            "[red]No ai-hats.yaml found[/]. Run: ai-hats config set -r <role> -p <provider>"
        )
        raise SystemExit(1)
    proj_cfg = ProjectConfig.from_yaml(project_path)
    overlay = proj_cfg.customizations.get(role, OverlayConfig())
    _apply_overlay_edits(
        overlay,
        add_trait=add_trait,
        remove_trait=remove_trait,
        add_rule=add_rule,
        remove_rule=remove_rule,
        add_skill=add_skill,
        remove_skill=remove_skill,
        injection_append=injection_append,
    )
    proj_cfg.customizations[role] = overlay
    proj_cfg.save(project_path)
    console.print(f"[green]Updated[/] (project) customizations for [bold]{role}[/]")
    _print_overlay("project", role, overlay)


@click.command()
def status():
    """Show current role, dependency tree, and health."""
    asm = _assembler()
    st = asm.status()

    # HATS-497: the role + tree section is role-dependent, but install
    # diagnostics in the Health section below are useful regardless of
    # whether a role is composed (e.g. answering "what version am I
    # running, where does it live" before init). Don't early-return on
    # missing role — fall through to the Health block.
    if not st["role"]:
        console.print("[yellow]No role active[/]")
    else:
        console.print(f"Role: [bold]{st['role']}[/]")
        console.print(f"Provider: {st['provider']}")

    # Dependency tree (HATS-421: each node tagged with source layer).
    if st["role"] and st["tree"]:
        provenance = st["tree"].get("provenance", {})

        def _tag(component_type: str, name: str) -> str:
            """Return a source-tag suffix like ``  (global)`` for a node.

            Defaults to ``built-in`` when no overlay claims the name.
            Empty for ``priorities`` and ``hooks`` (no layer notion).
            """
            label = provenance.get(component_type, {}).get(name, "built-in")
            color = {"global": "magenta", "project": "cyan"}.get(label, "dim")
            return f"  [{color}]({label})[/{color}]"

        tree = Tree(f"[bold]{st['tree']['name']}[/]")
        if st["tree"]["priorities"]:
            p_branch = tree.add("[dim]priorities[/]")
            for p in st["tree"]["priorities"]:
                p_branch.add(p)
        if st["tree"].get("traits"):
            t_branch = tree.add("[dim]traits[/]")
            for t in st["tree"]["traits"]:
                t_branch.add(f"{t}{_tag('traits', t)}")
        if st["tree"]["rules"]:
            r_branch = tree.add("[dim]rules[/]")
            for r in st["tree"]["rules"]:
                r_branch.add(f"{r}{_tag('rules', r)}")
        if st["tree"]["skills"]:
            s_branch = tree.add("[dim]skills[/]")
            for s in st["tree"]["skills"]:
                s_branch.add(f"{s}{_tag('skills', s)}")
        if st["tree"]["hooks"]:
            h_branch = tree.add("[dim]hooks[/]")
            for event, scripts in st["tree"]["hooks"].items():
                h_branch.add(f"{event}: {scripts}")
        console.print(tree)
        console.print(
            "[dim]Legend:[/] [dim](built-in)[/]  [magenta](global)[/]  [cyan](project)[/]"
        )

    # Health — HATS-497: prefixed with install-level diagnostics (version,
    # interpreter, venv, source, library, resolved-via, repo HEAD) so a
    # single ``config status`` answers both project-config and "where does
    # my ai-hats live" questions. Existing project-side checks
    # (imports.md, system_prompt) print after, with their OK/Missing icons.
    from .maintenance import _gather_install_info

    console.print("\n[bold]Health:[/]")
    for key, val in _gather_install_info().items():
        console.print(f"  {key}: [dim]{val}[/]", highlight=False)
    if st.get("health"):
        for component, status_val in st["health"].items():
            icon = "[green]OK[/]" if status_val == "OK" else "[red]Missing[/]"
            console.print(f"  {component}: {icon}", highlight=False)

    if st.get("errors"):
        console.print("\n[bold yellow]Errors:[/]")
        for err in st["errors"]:
            console.print(f"  [yellow]{err}[/]")


@click.command("show-prompt")
@click.option(
    "--role", "-r", default=None,
    help="Role to materialize (default: active_role from ai-hats.yaml).",
)
@click.option(
    "--provider", "-p", default=None,
    help="Provider for build_system_prompt formatting (default: configured).",
)
@click.option(
    "--stats",
    is_flag=True,
    help="Emit composition_stats JSON to stdout instead of the prompt text.",
)
def show_prompt(role: str | None, provider: str | None, stats: bool):
    """Print the system prompt the agent would see for ROLE.

    Pure read — composes the role and renders through the provider's
    build_system_prompt, no session/file/spawn. Runs through the same
    ``materialize_system_prompt`` pipeline step that future runtime
    consumers will share (HATS-452 Phase 1).

    Examples
    --------

    \b
    ai-hats config show-prompt                    # active role, text
    ai-hats config show-prompt --role architect   # any role
    ai-hats config show-prompt --stats            # structured JSON
    ai-hats config show-prompt | grep "E2E gate"  # marker presence
    """
    from ..pipeline.pipeline import build as build_pipeline
    from ..pipeline.steps.emit import EmitStdout
    from ..pipeline.steps.materialize import MaterializeSystemPrompt
    from ._helpers import _project_dir

    project_dir = _project_dir()

    # Build the preview pipeline in-process — same shape as
    # library/core/pipelines/preview.yaml (which is shipped as a
    # canonical reference). YAML loader resolves pipelines via the
    # installed package's bundled library and is therefore opaque to
    # the project's library_paths; the programmatic build keeps the
    # CLI runnable from any project layout.
    emit_key = "composition_stats" if stats else "system_prompt_text"
    emit_fmt = "json" if stats else "text"
    pipeline = build_pipeline(
        MaterializeSystemPrompt(),
        EmitStdout({"key": emit_key, "format": emit_fmt}),
        name="preview",
    )
    try:
        pipeline.run(
            project_dir=project_dir,
            role=role,
            provider=provider,
        )
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)


def do_bump(*, migrate_force: bool, check_branches: bool) -> int:
    """Run the bump pipeline in-process. Returns process exit code.

    HATS-407 + HATS-415 + HATS-470: ``bump`` is no longer exposed as
    ``ai-hats self bump`` — it's an internal operation reachable only
    via :mod:`ai_hats._bump_internal` (fresh-subprocess path used by
    ``self update``, HATS-400) or via ``self init`` after the in-process
    assembler hook.

    HATS-469: ``Assembler.bump`` was replaced by ``_refresh`` — the
    bump pipeline is now an explicit composition here:

    1. ``_run_v07_migration`` — v0.6 → v0.7 layout heal (CLI-kwarg-gated,
       kept outside ``_refresh`` to preserve a clean signature).
    2. ``_refresh(install_time=True)`` — registry replay + scaffold +
       canonical + hooks.
    3. ``_run_diagnostics`` — orphan warning + empty .agent/ note
       (user-initiated path).

    Safe-to-delete v0.6 files are swept transparently; user-edited
    files refuse with per-file guidance. ``migrate_force`` bypasses
    the refusal (one stderr WARN per file). ``check_branches`` warns
    when local branches modify paths slated for deletion. No
    auto-commit — review with ``git status`` and commit at leisure.
    """
    from ..assembler import AssemblyError
    from ..materialize import compose_for_role
    from ..migration_assert import assert_runtime_hooks_resolve
    from ..migration_backup import BackupError, snapshot_pre_bump

    asm = _assembler()
    backup_path = None
    try:
        # 0. HATS-549: pre-bump snapshot BEFORE any destructive step.
        # _run_v07_migration / registry healer / _migrate_layout_v4 all
        # mutate the project tree; the tarball under /tmp is the
        # always-on recovery handle. Hard-fail on BackupError —
        # proceeding without a snapshot defeats the safety guarantee.
        try:
            backup_path = snapshot_pre_bump(asm.project_dir, label="bump")
        except BackupError as be:
            console.print(f"[red]Pre-bump backup failed[/]: {be}")
            return 1

        # 1. v0.6→v0.7 layout heal — must run BEFORE registry (step 6
        # = _migrate_layout_v4 expects v0.7 tree).
        asm._run_v07_migration(force=migrate_force, check_branches=check_branches)

        # 2. Compose for the active role (or None if none set). Passed
        # through to _refresh which installs role git hooks.
        cfg = asm.project_config
        role_name = cfg.active_role or cfg.default_role
        result = compose_for_role(asm, role_name) if role_name else None

        # 3. Unified heal/install.
        asm._refresh(install_time=True, result=result)

        # 4. Diagnostics — user-initiated path; expects state report.
        asm._run_diagnostics()

        # 5. HATS-549 Phase 3: end-of-bump smoke-assert. Every hook
        # command path in .claude/settings.json{,.local} must resolve
        # to an existing file — otherwise Claude Code prints
        # "No such file or directory" on every matching tool call.
        # The error message carries the Phase 1 backup path so the
        # user has a one-liner recovery handle.
        assert_runtime_hooks_resolve(asm.project_dir, backup_path=backup_path)
    except AssemblyError as e:
        # HATS-415: render the user-edits refusal message via Rich so the
        # per-file guidance markup (`[bold]…[/]`, `[yellow]…[/]`) inside
        # ``render_user_edits_refusal`` displays cleanly. ``console.print``
        # auto-renders newline-separated Rich markup.
        console.print(f"[red]Bump refused[/]:\n{e}")
        return 1
    if result is None:
        console.print("[green]Bumped[/]: migrations + scaffold + canonical refreshed")
    else:
        console.print(f"[green]Bumped[/]: {result.name} (hooks re-installed)")
    console.print(
        "  [dim]💡 Direct `claude` reads only user-rules. "
        "Run `ai-hats execute [-r ROLE]` for role-loaded sessions.[/]"
    )
    # HATS-470: surface the trash-bin banner so the user knows where
    # snapshots from this bump live (if any).
    from ..safe_delete import session_summary as _trash_summary
    banner = _trash_summary()
    if banner:
        console.print(f"  [dim]{banner}[/]")
    return 0


