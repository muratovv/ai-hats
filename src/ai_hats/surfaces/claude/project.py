"""The claude configuration of the project a session runs in, as planning sees
it (ADR-0036 D2): read once before the plan by ``probe_project`` and handed in
as ``Host.project`` — the planner reads nothing itself.

The home probe's sibling, and separate for the reason its argument list shows:
the settings a session's status line is built from are chained across the
person's home AND the project (`user <- project <- local`), so no environment
alone names them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_hats.paths import claude_settings_chain, claude_status_line

from ..plan import Digested


@dataclass(frozen=True)
class ClaudeProject(Digested):
    """What this project's claude settings say that a session's plan needs."""

    #: The status line the person configured, as the last file naming a command
    #: spells it; ``None`` when no file in the chain names one.
    status_line: Mapping[str, Any] | None = None


def probe_project(environ: Mapping[str, str], cwd: Path) -> ClaudeProject:
    """The one read of the project's claude settings a plan is built on."""
    return ClaudeProject(status_line=claude_status_line(claude_settings_chain(environ, cwd)))


__all__ = ["ClaudeProject", "probe_project"]
