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
    from ai_hats_core.atomic_io import atomic_write_bytes, atomic_write_text  # noqa: F401
    from ai_hats_core.composition import (
        ComponentKind,  # noqa: F401
        CompositionError,  # noqa: F401
        CompositionIncompleteError,  # noqa: F401
        CompositionResult,  # noqa: F401
        ConsentPoint,  # noqa: F401
        ResolvedCheck,  # noqa: F401
        ResolvedComponent,  # noqa: F401
    )
    from ai_hats_core.git_env import scrubbed_git_env  # noqa: F401
    from ai_hats_core.locks import LockTimeoutError, file_lock  # noqa: F401
    from ai_hats_core.migrations import Migration, latest_step, run_pending  # noqa: F401
    from ai_hats_core.paths import default_project_dir  # noqa: F401
    from ai_hats_core.yaml_model import YamlModel  # noqa: F401

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
    home = _HOMES.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f"{__name__}.{home}"), name)
    globals()[name] = value  # bound once; later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_HOMES})


__all__ = sorted(_HOMES)
