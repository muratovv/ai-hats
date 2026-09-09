"""What an ``init`` run asks of the world — declared here, implemented at the root.

The three init steps (``pipeline/steps/init_steps.py``) read these; ``cli/assembly.py``
implements them with click and one ``Assembler``. A leaf on purpose: ``session_policy``
types ``InitRunParams`` with them and the pipeline area reads them, so the home has to
be one neither side reaches into.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from click import ClickException

from ..config import Channel
from ..config.project import ProjectConfig

if TYPE_CHECKING:
    from ..surfaces import Surface


class InitWizard(Protocol):
    """A human at a terminal, or a fake. ``select_provider`` is its only reader.

    The provider is chosen as a ``Surface`` — the registry's object, with its name,
    its detection and its install state — and travels the funnel by ``.name``: the
    config stores the name, ``ai-hats execute --provider`` takes the name.
    """

    def is_interactive(self) -> bool: ...

    def detected_providers(self) -> list[Surface]:
        """Surfaces whose home directory exists on this host, registry order."""
        ...

    def choose_provider(self, detected: list[Surface]) -> Surface:
        """Ask for one surface; ``detected`` only pre-selects a default when it is alone."""
        ...

    def choose_channel(self, current: Channel | None) -> Channel:
        """Ask where the harness installs ai-hats from: ``stable`` (PyPI), ``edge``
        (git main) or ``local`` (an editable checkout, ``harness_path``).

        ``current`` is the channel a re-initialized project already has — shown as
        the default; ``None`` on a bare directory, where the default is ``stable``.
        """
        ...


class ProjectBootstrapper(Protocol):
    """The ONE ``Assembler`` an init run builds — at the root, before the pipeline."""

    @property
    def config(self) -> ProjectConfig:
        """Defaults on a bare directory; fresh after ``init()``."""
        ...

    def init(
        self,
        *,
        provider: str,
        role: str | None,
        task_prefix: str | None,  # TODO(HATS-1886): rack's to own; init stops writing it
        # Relative to the project root — the framework dir the config names.
        ai_hats_dir: Path | None,
        # A user-owned venv to adopt instead of the managed ``<ai_hats_dir>/.venv``.
        venv_path: Path | None,
        manage_gitignore: bool | None,
        channel: Channel | None,
        # The editable checkout ``channel=local`` installs from.
        harness_path: Path | None,
    ) -> None:
        """Write config + scaffold; ``ValueError`` on bad input, nothing touched."""
        ...

    def verify_runtime_hooks(self, *, backup_path: Path | None) -> None:
        """Raise ``InitRefusedError`` when a declared hook command does not resolve."""
        ...

    def report_diagnostics(self) -> None: ...


class InitRefusedError(ClickException):
    """The run cannot continue; the message is the refusal the operator reads."""

    exit_code = 1


class InitProviderRequiredError(ClickException):
    """Non-TTY stdin, no provider flag, greenfield project: nothing to ask, nothing to assume."""

    exit_code = 2

    def __init__(self) -> None:
        super().__init__(
            "[red]No TTY and no flags[/]: cannot run interactive wizard.\n"
            "Pass --provider/-p (and optionally --role/-r), or run with "
            "--no-wizard to bootstrap a minimal config."
        )


class InitConfigUnreadableError(ClickException):
    """Re-init on a project whose existing config will not load."""

    exit_code = 2

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"[red]Existing ai-hats.yaml could not be read[/]: {reason}\n"
            "Refusing to continue: init would silently reset this project's provider. "
            "Fix or remove the config, then re-run."
        )
