"""Harness reliability primitives.

Universal post-run validation for reporting roles: zero-output guard,
timeout retry policy, and universal sub-agent
surface safety guard.
"""

from .surface_guard import SurfaceGuard, SurfaceGuardError, SurfaceGuardResult

__all__ = [
    "SurfaceGuard",
    "SurfaceGuardError",
    "SurfaceGuardResult",
]
