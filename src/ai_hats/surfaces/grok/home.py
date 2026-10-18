"""Where grok lives — ``$GROK_HOME`` or ``~/.grok``, grok's own rule."""

from __future__ import annotations

from pathlib import Path

GROK_HOME_ENV = "GROK_HOME"


def grok_home() -> Path:
    from ai_hats.paths import tool_home

    return tool_home("grok", GROK_HOME_ENV)


__all__ = ["GROK_HOME_ENV", "grok_home"]
