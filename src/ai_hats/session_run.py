"""Context-managed lifetime for one active ai-hats session run."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_hats_observe import Session, SessionManager

logger = logging.getLogger(__name__)


class SessionRun:
    """Own an active session and its runtime resources."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._cleanups: list[tuple[str, Callable[[], None]]] = []

    @classmethod
    def create(
        cls,
        session_manager: SessionManager,
        *,
        parent_session: str | None = None,
    ) -> SessionRun:
        return cls(session_manager.create_session(parent_session=parent_session))

    def defer(self, name: str, cleanup: Callable[[], None]) -> None:
        self._cleanups.append((name, cleanup))

    def warn(self, message: str) -> None:
        logger.warning("%s", message)
        self.session.log_sys(message)

    def close(self) -> None:
        pending: BaseException | None = None
        while self._cleanups:
            name, cleanup = self._cleanups.pop()
            try:
                cleanup()
            except Exception as exc:
                logger.warning("%s cleanup failed", name, exc_info=True)
                try:
                    self.session.log_sys(
                        f"{name.capitalize()} cleanup FAILED — {type(exc).__name__}: {exc}"
                    )
                except Exception as report_exc:
                    logger.warning(
                        "%s cleanup diagnostic failed: %s",
                        name,
                        report_exc,
                        exc_info=True,
                    )
            except BaseException as exc:
                if pending is None:
                    pending = exc
                else:
                    logger.warning("additional %s cleanup failure", name, exc_info=True)
        if pending is not None:
            raise pending

    def __enter__(self) -> SessionRun:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()
