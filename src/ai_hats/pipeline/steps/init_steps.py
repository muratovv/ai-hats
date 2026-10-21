"""Pipeline steps for project initialization (`ai-hats self init`).

The steps own no collaborator: the wizard, the one ``Assembler`` and the project
directory arrive in the state (``InitRunParams``), and what a step has to say goes
back out as ``notices`` for the runner to print. A refusal is an exception the
runner renders — a step never prints and never exits the process.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Mapping

from ai_hats.config import Channel
from ai_hats.initialization import (
    InitProviderRequiredError,
    InitRefusedError,
    InitWizard,
    ProjectBootstrapper,
)

from ..keys import (
    KEY_AI_HATS_DIR,
    KEY_BOOTSTRAPPER,
    KEY_CHANNEL,
    KEY_EXECUTE_CMD,
    KEY_HARNESS_PATH,
    KEY_NO_MANAGE_GITIGNORE,
    KEY_NO_WIZARD,
    KEY_NOTICES,
    KEY_PROJECT_DIR,
    KEY_PROVIDER,
    KEY_ROLE,
    KEY_TASK_PREFIX,
    KEY_VENV_PATH,
    KEY_WIZARD,
)
from ..step import Step, StepIO

# Decided by ``select_provider`` before anything is written: the later steps read it
# instead of looking at a yaml that ``bootstrap_project`` has just created.
ALREADY_INITIALIZED = "already_initialized"


def _wants_wizard(
    wizard: InitWizard, *, no_wizard: bool, provider: str | None, role: str | None
) -> bool:
    return not no_wizard and wizard.is_interactive() and not (provider and role)


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
            requires=frozenset({KEY_PROJECT_DIR, KEY_WIZARD, KEY_BOOTSTRAPPER}),
            optional=frozenset({KEY_PROVIDER, KEY_NO_WIZARD, KEY_CHANNEL, KEY_ROLE}),
            produces=frozenset({KEY_PROVIDER, KEY_CHANNEL, ALREADY_INITIALIZED}),
        )

    def run(
        self,
        *,
        project_dir: Path,
        wizard: InitWizard,
        bootstrapper: ProjectBootstrapper,
        provider: str | None = None,
        no_wizard: bool = False,
        channel: Channel | None = None,
        role: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ai_hats.constants import PROVIDER_CLAUDE
        from ai_hats.paths import PROJECT_CONFIG

        already = (project_dir / PROJECT_CONFIG).exists()
        use_wizard = _wants_wizard(wizard, no_wizard=no_wizard, provider=provider, role=role)

        if not use_wizard and not already and provider is None and role is None and not no_wizard:
            raise InitProviderRequiredError()

        res_channel = channel
        res_provider = provider

        if use_wizard:
            if res_channel is None and res_provider is None:
                current = bootstrapper.config.harness.channel if already else None
                res_channel = wizard.choose_channel(current)
            if res_provider is None:
                res_provider = wizard.choose_provider(wizard.detected_providers()).name

        if res_provider is None:
            res_provider = (bootstrapper.config.provider if already else None) or PROVIDER_CLAUDE

        out: dict[str, Any] = {KEY_PROVIDER: res_provider, ALREADY_INITIALIZED: already}
        if res_channel is not None:
            out[KEY_CHANNEL] = res_channel
        return out


class BootstrapProjectStep(Step):
    """Pre-init backup, then ``init`` on the run's one bootstrapper."""

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="bootstrap_project",
            requires=frozenset({KEY_PROJECT_DIR, KEY_PROVIDER, KEY_BOOTSTRAPPER}),
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
            produces=frozenset({KEY_NOTICES}),
        )

    def run(
        self,
        *,
        project_dir: Path,
        provider: str,
        bootstrapper: ProjectBootstrapper,
        role: str | None = None,
        task_prefix: str | None = None,
        ai_hats_dir: Path | None = None,
        venv_path: Path | None = None,
        no_manage_gitignore: bool = False,
        channel: Channel | None = None,
        harness_path: Path | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ai_hats.paths import PROJECT_CONFIG

        already = (project_dir / PROJECT_CONFIG).exists()
        agent_dir = project_dir / ".agent"
        agent_existed_before = agent_dir.exists()
        init_backup_path = None
        if already:
            from ai_hats.migration_backup import BackupError, snapshot_pre_bump

            try:
                init_backup_path = snapshot_pre_bump(project_dir, label="init")
            except BackupError as be:
                raise InitRefusedError(f"[red]Pre-init backup failed[/]: {be}") from be

        manage_gitignore: bool | None = False if no_manage_gitignore else None
        try:
            bootstrapper.init(
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
            raise InitRefusedError(f"[red]Error[/]: {err}") from err

        notices: list[str] = []
        seeded = bootstrapper.config.harness
        if seeded.channel is not Channel.STABLE:
            loc = f" → {seeded.path}" if seeded.path else ""
            notices.append(f"[green]✓[/] harness channel: [bold]{seeded.channel.value}[/]{loc}")

        if already:
            bootstrapper.verify_runtime_hooks(backup_path=init_backup_path)

        return {KEY_NOTICES: notices}


class PrepareExecuteSessionStep(Step):
    """Report the outcome and prepare the hand-off to `ai-hats execute`."""

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="prepare_execute_session",
            requires=frozenset({KEY_PROJECT_DIR, KEY_PROVIDER, KEY_WIZARD, KEY_BOOTSTRAPPER}),
            optional=frozenset(
                {
                    KEY_ROLE,
                    KEY_NO_WIZARD,
                    KEY_TASK_PREFIX,
                    KEY_AI_HATS_DIR,
                    KEY_VENV_PATH,
                    KEY_NO_MANAGE_GITIGNORE,
                    KEY_NOTICES,
                    ALREADY_INITIALIZED,
                }
            ),
            produces=frozenset({KEY_EXECUTE_CMD, KEY_NOTICES}),
        )

    def run(
        self,
        *,
        project_dir: Path,
        provider: str,
        wizard: InitWizard,
        bootstrapper: ProjectBootstrapper,
        role: str | None = None,
        no_wizard: bool = False,
        task_prefix: str | None = None,
        ai_hats_dir: Path | None = None,
        venv_path: Path | None = None,
        no_manage_gitignore: bool = False,
        notices: list[str] | None = None,
        already_initialized: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        already = already_initialized
        use_wizard = _wants_wizard(wizard, no_wizard=no_wizard, provider=provider, role=role)
        out: list[str] = list(notices or ())

        if already and not use_wizard:
            from ai_hats_core.safe_delete import session_summary as _trash_summary

            bootstrapper.report_diagnostics()
            banner = _trash_summary()
            if banner:
                out.append(f"  [dim]{banner}[/]")

        label = "Re-initialized" if already else "Initialized"
        out.append(f"[green]{label}[/] ai-hats in {project_dir}")

        cfg = bootstrapper.config
        if role:
            out.append(f"  Default role: [bold]{role}[/]")
        out.append(f"  Provider: [bold]{provider}[/]")
        if task_prefix:
            out.append(f"  Task prefix: [bold]{cfg.task_prefix}[/]")
        if ai_hats_dir:
            out.append(f"  ai-hats dir: [bold]{cfg.ai_hats_dir}[/]")
        if venv_path:
            out.append(f"  Venv path: [bold]{cfg.venv_path}[/]")
        if no_manage_gitignore:
            out.append("  manage_gitignore: [bold]disabled[/]")

        if not (use_wizard and not role):
            out.append(
                "  [dim]💡 A direct provider session reads no ai-hats content. "
                "Run `ai-hats execute [-r ROLE]` for role + user-rules.[/]"
            )

        if use_wizard and not role:
            ai_hats_bin = shutil.which("ai-hats")
            if not ai_hats_bin:
                out.append(
                    "[yellow]ai-hats binary not in PATH — cannot auto-launch wizard.[/]\n"
                    "Run manually:  ai-hats execute --role initial-wizard --prompt initial-wizard --provider "
                    + provider,
                )
                return {KEY_EXECUTE_CMD: None, KEY_NOTICES: out}

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
            return {KEY_EXECUTE_CMD: cmd, KEY_NOTICES: out}

        return {KEY_EXECUTE_CMD: None, KEY_NOTICES: out}
