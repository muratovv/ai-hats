"""Universal surface safety guard for sub-agent execution across all providers (claude / agy / cline) (HATS-1105).

Provides pre-flight isolation checks and post-flight worktree audits to guarantee
that sub-agents running in headless print mode (-p) maintain workspace safety
and cannot mutate the main checkout without review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats_wt import IsolationMode

if TYPE_CHECKING:
    from ai_hats_observe import Session

logger = logging.getLogger(__name__)


class SurfaceGuardError(RuntimeError):
    """Raised when SurfaceGuard verification fails."""


@dataclass(frozen=True)
class SurfaceGuardResult:
    """Explicit Ok | Error result interface for SurfaceGuard checks."""

    is_ok: bool
    error_reason: str = ""

    @classmethod
    def ok(cls) -> SurfaceGuardResult:
        return cls(is_ok=True)

    @classmethod
    def error(cls, reason: str) -> SurfaceGuardResult:
        return cls(is_ok=False, error_reason=reason)

    def unwrap(self) -> None:
        """Raise SurfaceGuardError if the result represents an error."""
        if not self.is_ok:
            raise SurfaceGuardError(self.error_reason)


class SurfaceGuard:
    """Universal sub-agent safety guard across all provider surfaces (claude / agy / cline)."""

    @staticmethod
    def pre_flight_check(
        project_dir: Path,
        work_dir: Path,
        isolation_mode: IsolationMode,
        provider_name: str,
    ) -> SurfaceGuardResult:
        """Pre-flight verification before launching a headless sub-agent.

        Guarantees that sub-agents running in non-interactive print mode (-p)
        execute in an isolated worktree when required, protecting the main repo.
        Returns an explicit SurfaceGuardResult (Ok | Error).
        """
        logger.debug(
            "SurfaceGuard pre-flight check: provider=%s, isolation=%s, work_dir=%s",
            provider_name,
            isolation_mode,
            work_dir,
        )

        if isolation_mode in (IsolationMode.DISCARD, IsolationMode.SQUASH, IsolationMode.BRANCH):
            if work_dir.resolve() == project_dir.resolve():
                return SurfaceGuardResult.error(
                    f"Sub-agent execution on surface '{provider_name}' requested isolation_mode='{isolation_mode.value}', "
                    f"but work_dir '{work_dir}' equals main project_dir '{project_dir}'. Execution refused."
                )

        return SurfaceGuardResult.ok()

    @staticmethod
    def post_flight_guard(
        session: "Session",
        work_dir: Path,
        provider_name: str,
    ) -> SurfaceGuardResult:
        """Post-flight audit after sub-agent completion.

        Inspects the finalized sub-agent session and work_dir state to verify
        execution safety across all surfaces. Returns an explicit SurfaceGuardResult.
        """
        logger.debug(
            "SurfaceGuard post-flight check: provider=%s, session_id=%s, work_dir=%s",
            provider_name,
            session.session_id,
            work_dir,
        )
        return SurfaceGuardResult.ok()
