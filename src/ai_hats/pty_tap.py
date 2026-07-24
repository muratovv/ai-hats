"""PTY-tap seam contract (HATS-1192).

Lets an out-of-tree plugin observe/drive a live session PTY with no relay code in
core. A plugin (ai-hats-relay, HATS-1197) supplies a factory via pipeline
composition; ``_pty_spawn`` calls it. No factory seeded → the seam is inert
(byte-for-byte historical behaviour). Rationale + wiring: tasks/HATS-1192.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


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
