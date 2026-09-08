"""Typed launch parameters for a form-capable stdio MCP server."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StdioMCPServer:
    name: str
    command: str
    args: tuple[str, ...]
    cwd: Path
    env_vars: tuple[str, ...]
    startup_timeout_s: int
    tool_timeout_s: int
