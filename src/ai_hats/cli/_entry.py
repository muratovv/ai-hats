"""The composition root — the only home of ``resolve_project`` and ``project_at``.

Its importers are the process entry points, and only them: the deny-by-default
pin in tests/test_import_hygiene.py holds the door, so a deep module importing
this one goes red. ``project.py`` deliberately ships the TYPE without the
factory — deep code declares ``project: Project`` and receives it; the
Assembler precedent (built four times by one ``ai-hats init``) is the disease
this split prevents.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Mapping

import click
from ai_hats_core.layout import (
    ForeignPinPolicy,
    ProjectLayout,
    ProjectNotFoundError,
    pin_is_foreign,
    resolve_root,
)

from ..config.project import ProjectConfig
from ..env import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_VENV
from ..paths import PROJECT_CONFIG, ProjectConfigError, builtin_library_layers
from ..project import Project
from ..session_identity import SessionIdentity
from ..version_refs import read_current_sha


def resolve_project(
    start: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Project:
    """Build the process's ONE ``Project``.

    Inside a live session the value is DESERIALIZED, never re-derived (R6): the
    envelope's ``project_dir`` wins over any filesystem walk, so the historical
    resolvers cannot diverge here — they are not consulted at all. An envelope
    that is present but unreadable raises rather than falling back: "no
    session" and "a session we cannot read" are different answers.
    """
    env = dict(os.environ if environ is None else environ)
    root = _resolve_root(start, env)
    return _assemble(root, _load_config(root), env)


def resolve_project_lenient(
    start: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Project:
    """The same ``Project``, for a command that must answer anyway.

    Diagnostics and repair are the callers: refusing to say what version is
    running because no project is here, or refusing to repair a project because
    its config is the broken thing, makes the command useless exactly where it
    is needed. Both degradations are ANNOUNCED — a silent default would hide
    the breakage the user is asking about.

    The happy path stays ``resolve_project``: this is a wrapper around it, never
    a second way to build a ``Project``.
    """
    env = dict(os.environ if environ is None else environ)
    try:
        return resolve_project(start, environ)
    except ProjectNotFoundError:
        root = start or Path.cwd()
        click.echo(
            f"Warning: no ai-hats project above {root} — answering for this directory.",
            err=True,
        )
        return _assemble(root, _load_config(root), env)
    except ProjectConfigError as exc:
        root = _resolve_root(start, env)  # it resolved; the config is what failed
        click.echo(f"Warning: {root / PROJECT_CONFIG} will not load — using defaults.", err=True)
        click.echo(f"  {exc}", err=True)
        return _assemble(root, ProjectConfig(), env)


def _resolve_root(start: Path | None, env: Mapping[str, str]) -> Path:
    identity = SessionIdentity.from_env(env)
    if identity is not None:
        return Path(identity.project_dir)
    if start is None:
        try:
            start = Path.cwd()
            start.stat()
        except OSError as exc:  # the worktree under our feet was torn down
            raise DeadCwdError() from exc
    return resolve_root(start, env, on_foreign_pin=ForeignPinPolicy.WARN_AND_IGNORE)


def project_at(root: Path, environ: Mapping[str, str] | None = None) -> Project:
    """The ``Project`` AT ``root`` — no walk, no envelope.

    For a caller that already holds the root and may mean a project other than the
    session's own: rack hands the integrator its resolved root, and ``--tasks-dir``
    can point at a backlog owner the envelope does not name. ``resolve_project``'s
    envelope-wins rule would answer the wrong project there.
    """
    env = dict(os.environ if environ is None else environ)
    return _assemble(root, _load_config(root), env)


def _assemble(root: Path, config: ProjectConfig, env: Mapping[str, str]) -> Project:
    layout = ProjectLayout.compute(root, env, ai_hats_dir=config.ai_hats_dir)
    return Project(
        layout=layout,
        config=config,
        venv=_venv(layout, config, env),
        library_paths=_library_layers(layout, config),
    )


class DeadCwdError(click.ClickException):
    """The current working directory no longer exists.

    Commonly: the linked worktree you were standing in was just torn down by
    `rack transition <id> done` / `wt merge`. Resolving the project root from a
    removed cwd would otherwise crash (`Path.cwd()` → FileNotFoundError on
    macOS) or, on Linux where `os.getcwd()` can return a stale path string,
    silently fall through to a cwd fallback and resurrect a phantom `.agent/`
    tracker. Fail loud instead and point the operator back to a real directory.
    """

    def __init__(self) -> None:
        super().__init__(
            "Current directory no longer exists (a worktree you were in may "
            "have just been removed). cd to your project root and re-run."
        )


def _load_config(root: Path) -> ProjectConfig:
    """The full reader, with its migrations and fail-loud-on-newer.

    A bare project is defaults, not an error — ``init`` runs before any yaml
    exists.
    """
    return ProjectConfig.from_yaml(root / PROJECT_CONFIG)


def _venv(layout: ProjectLayout, config: ProjectConfig, env: Mapping[str, str]) -> Path:
    """env (under the pin's scoped trust) > config.venv_path > the current managed version > default.

    The launcher mirrors this chain in bash; parity is held by a conformance
    test, not shared code.
    """
    raw = env.get(ENV_AI_HATS_VENV)
    if raw:
        pin = env.get(AI_HATS_PROJECT_DIR_ENV)
        if pin_is_foreign(pin, layout.root):
            warnings.warn(
                f"{ENV_AI_HATS_VENV}={raw!r} is pinned to project {pin!r} — foreign to "
                f"{layout.root}; ignoring the leaked session pin.",
                stacklevel=2,
            )
        else:
            return Path(raw).expanduser()
    if config.venv_path:
        candidate = Path(config.venv_path).expanduser()
        return candidate if candidate.is_absolute() else layout.root / candidate
    sha = read_current_sha(layout.versions)
    if sha is not None:
        return layout.versions.dir(sha)
    return layout.default_venv


def _library_layers(layout: ProjectLayout, config: ProjectConfig) -> tuple[Path, ...]:
    """Builtin layers first (lowest priority), then the project's extra roots."""
    extra = []
    for raw in config.library_paths:
        candidate = Path(raw).expanduser()
        extra.append(candidate if candidate.is_absolute() else layout.root / candidate)
    return (*builtin_library_layers(layout.root), *extra)
