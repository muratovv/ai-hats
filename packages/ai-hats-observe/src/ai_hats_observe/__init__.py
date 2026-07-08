"""Standalone session-logging engine (ADR-0014 Phase 1, T15).

The observability core extracted from the ``ai_hats`` integrator: session
lifecycle + a surface-agnostic trace/audit writer. Imports only ``ai_hats_core``,
never the integrator. Exports are lazy (PEP 562) so importing a vocab leaf
(``.trace`` / ``.artifacts``) never drags the writer — ``ai_hats.paths`` /
``auto_retro`` stay cheap, while ``from ai_hats_observe import SessionManager``
still resolves via ``__getattr__``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # re-export stubs for static checkers; runtime uses __getattr__
    from .audit import AuditWriter  # noqa: F401
    from .parsers.base import Turn  # noqa: F401
    from .parsers.trace import TraceEntry  # noqa: F401
    from .session import Session, SessionManager  # noqa: F401
    from .sidecar import SidecarTracer  # noqa: F401

# public name -> owning submodule (resolved lazily by __getattr__)
_LAZY_EXPORTS = {
    "SessionManager": "session",
    "Session": "session",
    "SidecarTracer": "sidecar",
    "AuditWriter": "audit",
    "TraceEntry": "parsers.trace",
    "Turn": "parsers.base",
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{submodule}", __name__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return [*globals(), *__all__]
