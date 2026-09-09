"""Pipeline steps for project initialization (`ai-hats self init`)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Mapping

from click import ClickException

from ..keys import (
    KEY_AI_HATS_DIR,
    KEY_CHANNEL,
    KEY_EXECUTE_CMD,
    KEY_HARNESS_PATH,
    KEY_NO_MANAGE_GITIGNORE,
    KEY_NO_WIZARD,
    KEY_PROJECT_DIR,
    KEY_PROVIDER,
    KEY_ROLE,
    KEY_TASK_PREFIX,
    KEY_VENV_PATH,
)
from ..step import Step, StepIO


class InitProviderRequiredError(ClickException):
    """Raised when stdin is non-TTY, no provider flag is given, and project is greenfield."""

    exit_code = 2

    def __init__(self, message: str = "No TTY and no --provider flag") -> None:
        super().__init__(
            "[red]No TTY and no flags[/]: cannot run interactive wizard.\n"
            "Pass --provider/-p (and optionally --role/-r), or run with "
            "--no-wizard to bootstrap a minimal config."
        )


class InitConfigUnreadableError(ClickException):
    """Raised when re-running init on a project whose existing config will not load."""

    exit_code = 2

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"[red]Existing ai-hats.yaml could not be read[/]: {reason}\n"
            "Refusing to continue: init would silently reset this project's provider. "
            "Fix or remove the config, then re-run."
        )


class SelectProviderStep(Step):
    """Select or resolve the provider for project initialization.

    GUARANTEE: `provider` in produces is guaranteed to be non-null and valid.
    """

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="select_provider",
            requires=frozenset({KEY_PROJECT_DIR}),
            optional=frozenset({KEY_PROVIDER, KEY_NO_WIZARD, KEY_CHANNEL, KEY_ROLE}),
            produces=frozenset({KEY_PROVIDER, KEY_CHANNEL}),
        )

    def run(
        self,
        *,
        project_dir: Path,
        provider: str | None = None,
        no_wizard: bool = False,
        channel: str | None = None,
        role: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ai_hats.constants import PROVIDER_CLAUDE
        from ai_hats.paths import PROJECT_CONFIG
        from ai_hats.cli.assembly import (
            _assembler,
            _detected_providers,
            _stdin_is_tty,
            _wizard_harness_prompt,
            _wizard_provider_prompt,
        )

        already = (project_dir / PROJECT_CONFIG).exists()
        use_wizard = not no_wizard and _stdin_is_tty() and not (provider and role)

        if not use_wizard and not already and provider is None and role is None and not no_wizard:
            raise InitProviderRequiredError()

        res_channel = channel
        res_provider = provider

        if use_wizard:
            if res_channel is None and res_provider is None:
                cur_ch = None
                if already:
                    try:
                        cur_ch = _assembler(project_dir).project_config.harness.channel.value
                    except Exception:  # noqa: S110
                        # silent-ok: only prefills the wizard default; having none is fine
                        pass
                res_channel = _wizard_harness_prompt(cur_ch)

            if res_provider is None:
                detected = _detected_providers()
                res_provider = _wizard_provider_prompt(detected)

        if res_provider is None:
            if already:
                try:
                    res_provider = _assembler(project_dir).project_config.provider
                except Exception as exc:
                    # Swallowing this reset an existing project to claude with no
                    # message — an agy/cline project silently reconfigured by a
                    # re-run of init.
                    raise InitConfigUnreadableError(repr(exc)) from exc
            if res_provider is None:
                res_provider = PROVIDER_CLAUDE

        out: dict[str, Any] = {KEY_PROVIDER: res_provider}
        if res_channel is not None:
            out[KEY_CHANNEL] = res_channel
        return out


class BootstrapProjectStep(Step):
    """Execute pre-init backups, migrations, and `Assembler.init()`."""

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="bootstrap_project",
            requires=frozenset({KEY_PROJECT_DIR, KEY_PROVIDER}),
            optional=frozenset(
                {
                    KEY_ROLE,
                    KEY_TASK_PREFIX,
                    KEY_AI_HATS_DIR,
                    KEY_VENV_PATH,
                    KEY_NO_MANAGE_GITIGNORE,
                    KEY_CHANNEL,
                    KEY_HARNESS_PATH,
                }
            ),
            produces=frozenset(),
        )

    def run(
        self,
        *,
        project_dir: Path,
        provider: str,
        role: str | None = None,
        task_prefix: str | None = None,
        ai_hats_dir: str | None = None,
        venv_path: str | None = None,
        no_manage_gitignore: bool = False,
        channel: str | None = None,
        harness_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ai_hats.paths import PROJECT_CONFIG
        from ai_hats.cli.assembly import _assembler, console
        from ai_hats.models import Channel as _Channel

        already = (project_dir / PROJECT_CONFIG).exists()
        agent_dir = project_dir / ".agent"
        agent_existed_before = agent_dir.exists()
        init_backup_path = None
        if already:
            from ai_hats.migration_backup import BackupError, snapshot_pre_bump

            try:
                init_backup_path = snapshot_pre_bump(project_dir, label="init")
            except BackupError as be:
                console.print(f"[red]Pre-init backup failed[/]: {be}")
                raise SystemExit(1) from be

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
                channel=channel,
                harness_path=harness_path,
            )
        except ValueError as err:
            if (
                not already
                and not agent_existed_before
                and agent_dir.exists()
                and not (project_dir / PROJECT_CONFIG).exists()
            ):
                shutil.rmtree(agent_dir, ignore_errors=True)  # safe-delete: ok init-cleanup
            console.print(f"[red]Error[/]: {err}")
            raise SystemExit(1) from err

        seeded = asm.project_config.harness
        if seeded.channel is not _Channel.STABLE:
            loc = f" → {seeded.path}" if seeded.path else ""
            console.print(f"[green]✓[/] harness channel: [bold]{seeded.channel.value}[/]{loc}")

        if already:
            from ai_hats.assembler import AssemblyError as _AssemblyError
            from ai_hats.migration_assert import assert_runtime_hooks_resolve

            try:
                assert_runtime_hooks_resolve(
                    project_dir,
                    backup_path=init_backup_path,
                )
            except _AssemblyError as e:
                console.print(f"[red]Init refused[/]:\n{e}")
                raise SystemExit(1) from e

        return {}


class PrepareExecuteSessionStep(Step):
    """Prepare the command line to hand off process to `ai-hats execute`."""

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="prepare_execute_session",
            requires=frozenset({KEY_PROJECT_DIR, KEY_PROVIDER}),
            optional=frozenset(
                {
                    KEY_ROLE,
                    KEY_NO_WIZARD,
                    KEY_TASK_PREFIX,
                    KEY_AI_HATS_DIR,
                    KEY_VENV_PATH,
                    KEY_NO_MANAGE_GITIGNORE,
                }
            ),
            produces=frozenset({KEY_EXECUTE_CMD}),
        )

    def run(
        self,
        *,
        project_dir: Path,
        provider: str,
        role: str | None = None,
        no_wizard: bool = False,
        task_prefix: str | None = None,
        ai_hats_dir: str | None = None,
        venv_path: str | None = None,
        no_manage_gitignore: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        from ai_hats.paths import PROJECT_CONFIG
        from ai_hats.cli.assembly import _assembler, _stdin_is_tty, console

        already = (project_dir / PROJECT_CONFIG).exists()
        asm = _assembler(project_dir)
        use_wizard = not no_wizard and _stdin_is_tty() and not (provider and role)

        if already and not use_wizard:
            from ai_hats_core.safe_delete import session_summary as _trash_summary

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
        if no_manage_gitignore:
            console.print("  manage_gitignore: [bold]disabled[/]")

        if not (use_wizard and not role):
            console.print(
                "  [dim]💡 A direct provider session reads no ai-hats content. "
                "Run `ai-hats execute [-r ROLE]` for role + user-rules.[/]"
            )

        if use_wizard and not role:
            ai_hats_bin = shutil.which("ai-hats")
            if not ai_hats_bin:
                console.print(
                    "[yellow]ai-hats binary not in PATH — cannot auto-launch wizard.[/]\n"
                    "Run manually:  ai-hats execute --role initial-wizard --prompt initial-wizard --provider "
                    + provider,
                )
                return {KEY_EXECUTE_CMD: None}

            cmd = [
                ai_hats_bin,
                "execute",
                "--role",
                "initial-wizard",
                "--prompt",
                "initial-wizard",
                "--provider",
                provider,
            ]
            return {KEY_EXECUTE_CMD: cmd}

        return {KEY_EXECUTE_CMD: None}
