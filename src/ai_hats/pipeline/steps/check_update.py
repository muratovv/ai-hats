"""``check_update_async`` step — detached background probe for upstream SHA.

Spawns ``python -m ai_hats.update_check <project_dir>`` with ``DEVNULL``
descriptors and ``start_new_session=True`` so the child outlives the
pipeline run if needed. ``failure_policy = "continue"`` — any failure here
is a quiet no-op; the Update banner will simply skip rendering until a
later session's probe lands.

Stale-while-revalidate: if a cache entry already exists and is within TTL,
no new subprocess is spawned. Otherwise the probe fires and the banner step
later in this same pipeline reads whatever's currently on disk (possibly
the previous, stale entry).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from ...update_check import is_disabled, read_cache
from ..step import Step, StepIO


class CheckUpdateAsync(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="check_update_async",
            requires=frozenset({"project_dir"}),
        )

    def run(self, *, project_dir: Path, **_: Any) -> dict[str, Any]:
        if is_disabled():
            return {}
        cached = read_cache(project_dir)
        if cached is not None and cached.is_fresh:
            return {}
        try:
            subprocess.Popen(
                [sys.executable, "-m", "ai_hats.update_check", str(project_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            # Quiet fallback — never break a session over a missing python.
            pass
        return {}
