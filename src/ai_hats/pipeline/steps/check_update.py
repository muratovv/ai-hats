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

from ...update_check import (
    detect_installed_sha,
    is_disabled,
    is_local_channel,
    read_cache,
    sha_matches,
)
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
        # HATS-781: a LOCAL editable harness is updated via ``git`` — never
        # probe upstream or surface a ``self update`` nudge for it.
        if is_local_channel(project_dir):
            return {}
        cached = read_cache(project_dir)
        if cached is not None and cached.is_fresh:
            # HATS-781: the cache is keyed only on project_dir + 24h TTL. A
            # reinstall within that window changes the installed SHA, so a
            # time-fresh cache can still describe the PRE-update build. Re-probe
            # when the running SHA is known AND differs; when it is unknown
            # (None) keep skipping rather than churn a probe every session.
            current = detect_installed_sha()
            if current is None or sha_matches(cached.installed_sha, current):
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
