"""PTY-tap seam contract (HATS-1192).

Lets an out-of-tree plugin observe/drive a live session PTY with no relay code in
core. A plugin (ai-hats-relay, HATS-1197) supplies a factory via pipeline
composition; ``_pty_spawn`` calls it. No factory seeded → the seam is inert
(byte-for-byte historical behaviour). Rationale + wiring: tasks/HATS-1192.
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

PTY_TAP_ENTRY_POINT_GROUP = "ai_hats.pty_tap"


def _pty_tap_entry_points():
    """Entry points advertised under the pty_tap group (isolated for tests)."""
    return importlib.metadata.entry_points(group=PTY_TAP_ENTRY_POINT_GROUP)


def load_pty_tap_factory() -> PtyTapFactory | None:
    """Load and return registered PtyTapFactory from entry points or built-in FdTap.

    If an entry point is registered, uses it.
    Otherwise returns the built-in make_fd_pty_tap factory.
    """
    eps = list(_pty_tap_entry_points())
    if eps:
        if len(eps) > 1:
            names = [getattr(ep, "name", str(ep)) for ep in eps]
            logger.warning(
                "Multiple ai_hats.pty_tap entry points found (%s); using '%s'",
                names,
                eps[0].name,
            )
        try:
            factory = eps[0].load()
            if callable(factory):
                return factory
            logger.warning("ai_hats.pty_tap entry point '%s' did not return a callable", eps[0].name)
        except Exception as exc:
            logger.warning("Failed to load ai_hats.pty_tap entry point '%s': %s", eps[0].name, exc)

    from .pty_relay import make_fd_pty_tap

    return make_fd_pty_tap



@runtime_checkable
class PtyTap(Protocol):
    """Per-session PTY observer/driver, built by a ``PtyTapFactory``.

    Methods run inside the ``_pty_spawn`` select loop and MUST NOT block (the
    interactive session is sacred). The caller swallows tap faults (fail-open).
    """

    def extra_read_fds(self) -> list[int]:
        """FDs to add to the loop's ``select`` read-set; re-queried each iteration."""
        ...

    def on_output(self, data: bytes) -> None:
        """Receive a copy of bytes already written to the terminal (a tee, not a filter)."""
        ...

    def on_readable(self, fd: int) -> None:
        """Handle readability on one of ``extra_read_fds()``."""
        ...

    def close(self) -> None:
        """Release resources; should be idempotent (may be called more than once)."""
        ...


class NullPtyTap:
    """No-op tap — the default when no plugin is composed.

    Every HITL session uniformly "has a tap" (just an inert one), so the
    interactive loop carries no ``None`` guards.
    """

    def extra_read_fds(self) -> list[int]:
        return []

    def on_output(self, data: bytes) -> None:
        pass

    def on_readable(self, fd: int) -> None:
        pass

    def close(self) -> None:
        pass


# Funnel contract: a factory travels (a live tap needs the master fd, born only
# inside _pty_spawn). Called factory(*, inject, resize, session) -> PtyTap.
PtyTapFactory = Callable[..., PtyTap]
