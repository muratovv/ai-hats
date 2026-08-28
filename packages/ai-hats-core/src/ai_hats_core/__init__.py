"""Core primitives and shared mechanisms for the ai-hats framework.

Minimal dependencies, each load-bearing (HATS-862 F2: pydantic — the base of
the model layer; HATS-526: filelock — the cross-process lock primitive).
No domain schemas (ADR-0014 core contract).

Bound LAZILY (PEP 562): the dependency set is unchanged, only the moment it
loads — reaching `ai_hats_core.deadline`, pure stdlib and on the hook path of
every tool call, must not cost pydantic and filelock. ADR-0014 Amendments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the names above resolve for a reader and a type checker
    from ai_hats_core.atomic_io import atomic_write_bytes, atomic_write_text
    from ai_hats_core.composition import (
        ComponentKind,
        CompositionError,
        CompositionIncompleteError,
        CompositionResult,
        ConsentPoint,
        ResolvedCheck,
        ResolvedComponent,
    )
    from ai_hats_core.git_env import scrubbed_git_env
    from ai_hats_core.locks import LockTimeoutError, file_lock
    from ai_hats_core.migrations import Migration, latest_step, run_pending
    from ai_hats_core.paths import default_project_dir
    from ai_hats_core.yaml_model import YamlModel

_HOMES = {
    "ComponentKind": "composition",
    "CompositionError": "composition",
    "CompositionIncompleteError": "composition",
    "CompositionResult": "composition",
    "ConsentPoint": "composition",
    "LockTimeoutError": "locks",
    "Migration": "migrations",
    "ResolvedCheck": "composition",
    "ResolvedComponent": "composition",
    "YamlModel": "yaml_model",
    "atomic_write_bytes": "atomic_io",
    "atomic_write_text": "atomic_io",
    "default_project_dir": "paths",
    "file_lock": "locks",
    "latest_step": "migrations",
    "run_pending": "migrations",
    "scrubbed_git_env": "git_env",
}


def __getattr__(name: str) -> object:
    from importlib import import_module

    home = _HOMES.get(name)
    if home is not None:
        value = getattr(import_module(f"{__name__}.{home}"), name)
        globals()[name] = value  # bound once; later lookups skip __getattr__
        return value
    if name.startswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # Eager binding used to expose the submodules this facade imported; keep it.
    try:
        return import_module(f"{__name__}.{name}")
    except ModuleNotFoundError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted({*globals(), *_HOMES})


__all__ = [
    "ComponentKind",
    "CompositionError",
    "CompositionIncompleteError",
    "CompositionResult",
    "LockTimeoutError",
    "Migration",
    "ConsentPoint",
    "ResolvedCheck",
    "ResolvedComponent",
    "YamlModel",
    "atomic_write_bytes",
    "atomic_write_text",
    "default_project_dir",
    "file_lock",
    "latest_step",
    "run_pending",
    "scrubbed_git_env",
]
