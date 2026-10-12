"""Where kimi lives — ``$KIMI_CODE_HOME`` or ``~/.kimi-code``, kimi's own rule."""

from __future__ import annotations

from pathlib import Path

KIMI_HOME_ENV = "KIMI_CODE_HOME"


def kimi_home() -> Path:
    from ai_hats.paths import tool_home

    return tool_home("kimi-code", KIMI_HOME_ENV)


__all__ = ["KIMI_HOME_ENV", "kimi_home"]
