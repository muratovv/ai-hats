"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal

from .broker import Broker
from .server import serve_broker, shutdown


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
    parser.add_argument(
        "--provider",
        default="",
        help="provider override for spawned sessions (default: whatever the project resolves)",
    )
    parser.add_argument("--cwd", default=None, help="working directory for spawned sessions")
    parser.add_argument(
        "--web", action="store_true", help="also serve the browser client on the same port"
    )
    parser.add_argument(
        "--token",
        # An argv token is readable in `ps` by anyone else on the box; the env var is
        # the way to keep it out of there.
        default=os.environ.get("HATS_RELAY_TOKEN", ""),
        help="shared token every client must present, required (env: HATS_RELAY_TOKEN)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def make_argv_builder(binary: str, provider: str = ""):
    def argv_for(spec: dict) -> list[str]:
        argv = [binary]
        if provider:
            argv += ["-p", provider]
        return [*argv, "-r", spec["role"]]

    return argv_for


async def run(args: argparse.Namespace) -> int:
    broker = Broker(make_argv_builder(args.binary, args.provider), cwd=args.cwd)
    server = await serve_broker(
        broker, host=args.host, port=args.port, web_client=args.web, token=args.token
    )

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))

    logging.info("listening on ws://%s:%s", args.host, args.port)
    if args.web:
        logging.info(
            "browser client on http://%s:%s/?sid=<sid>&token=<token> — "
            "hats-relay-attach prints the whole link",
            args.host,
            args.port,
        )
    try:
        await stop
    finally:
        await shutdown(server, broker)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.token:
        parser.error("--token is required (or HATS_RELAY_TOKEN): a session is an agent "
                     "with a shell, and reaching the port must not be the whole right to drive it")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
