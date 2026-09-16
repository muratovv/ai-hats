"""One session on the plan: probe the machine, plan, launch, record (ADR-0036 D2–D6).

Everything here takes the plan's composition half and knows nothing of the
composer's own value — the same path serves whichever processing produced it.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from .surfaces import Host


def probe_host(
    environ: Mapping[str, str] | None = None,
    *,
    python: str = sys.executable,
    which: Callable[..., str | None] = shutil.which,
) -> Host:
    """The one read of the machine planning is allowed, taken before it.

    Every command the consent gate can wrap is resolved here, whether or not
    the composition asks for it: the host is a fact of the machine, not of the
    role, and a planner that finds no key refuses on its own terms.
    """
    from ai_hats_library.hooks.consent_gate import operations

    from .consent_wrapper import original_lookup_path

    env = os.environ if environ is None else environ
    path = original_lookup_path(env.get("PATH", ""))
    commands = {
        name: Path(found)
        for name in operations.wrapped_surfaces(operations.REGISTRY)
        if (found := which(name, path=path))
    }
    return Host(python=Path(python), path=path, commands=commands)


__all__ = ["probe_host"]
