"""Run stdio MCP with cancellable input and signal-driven cleanup."""

from __future__ import annotations

import logging
import os
import signal
import sys
from typing import TextIO

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server

logger = logging.getLogger(__name__)


class _Stdin(anyio.AsyncFile[str]):
    def __init__(self, stream: TextIO) -> None:
        super().__init__(stream)
        self._buffer = b""

    async def readline(self) -> str:
        # SDK's threaded readline cannot be cancelled while the client holds stdin open.
        while b"\n" not in self._buffer:
            await anyio.wait_readable(self.wrapped.fileno())
            chunk = os.read(self.wrapped.fileno(), 65536)
            if not chunk:
                line, self._buffer = self._buffer, b""
                return line.decode("utf-8", errors="replace")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return (line + b"\n").decode("utf-8", errors="replace")


async def serve(server: FastMCP) -> None:
    with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
        async with anyio.create_task_group() as group:

            async def stop_on_signal() -> None:
                async for received in signals:
                    logger.info("Stopping consent server on signal %s", received)
                    group.cancel_scope.cancel()
                    return

            group.start_soon(stop_on_signal)
            try:
                async with stdio_server(stdin=_Stdin(sys.stdin)) as (reader, writer):
                    await server._mcp_server.run(
                        reader, writer, server._mcp_server.create_initialization_options()
                    )
            finally:
                group.cancel_scope.cancel()
