"""Harness reliability primitives (HATS-378, HATS-1105).

Universal post-run validation for reporting roles: zero-output guard
(HATS-323), timeout retry policy (HATS-321), and universal sub-agent
surface safety guard (HATS-1105).
"""

from .surface_guard import SurfaceGuard, SurfaceGuardError, SurfaceGuardResult

__all__ = [
    "SurfaceGuard",
    "SurfaceGuardError",
    "SurfaceGuardResult",
]


