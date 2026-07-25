"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal

from .broker import Broker
from .server import serve_broker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hats-relay",
        description="Hold many ai-hats sessions and fan each one out to many clients. "
        "No authentication: run this only on a network you trust.",
    )
    # Required, not defaulted: with no authentication, reaching the port is the whole
    # right to drive a session, so the interface must be chosen rather than inherited.
    parser.add_argument("--host", required=True, help="interface to bind (no default, on purpose)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--binary", default="ai-hats", help="the ai-hats entry point to spawn")
    parser.add_argument("--cwd", default=None, help="working directory for spawned sessions")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def make_argv_builder(binary: str):
    def argv_for(spec: dict) -> list[str]:
        return [binary, "-r", spec["role"]]

    return argv_for


async def run(args: argparse.Namespace) -> int:
    broker = Broker(make_argv_builder(args.binary), cwd=args.cwd)
    server = await serve_broker(broker, host=args.host, port=args.port)

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))

    logging.info("listening on ws://%s:%s", args.host, args.port)
    try:
        await stop
    finally:
        server.close()
        await server.wait_closed()
        await broker.aclose()
    return 0


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
