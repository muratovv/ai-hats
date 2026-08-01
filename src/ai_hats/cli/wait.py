"""``ai-hats wait`` — block the live session until an event happens (HATS-986).

The three exit codes are the point. A shell ``until`` loop cannot tell "not true
yet" from "predicate is broken", so a typo'd predicate waits forever and the
silence reads exactly like a successful wait.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone

import click

EXIT_HAPPENED = 0
EXIT_PREDICATE_BROKEN = 2
EXIT_TIMEOUT = 124

_TRUE = 0
_NOT_YET = 1


def _probe(command: str) -> tuple[int, str]:
    """Run the predicate once; return (exit code, stderr)."""
    # shell=True is the interface, not an oversight: --until-cmd IS a shell snippet.
    completed = subprocess.run(  # noqa: S602
        command, shell=True, capture_output=True, text=True
    )
    return completed.returncode, completed.stderr


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@click.command("wait")
@click.option(
    "--until-cmd",
    "until_cmd",
    required=True,
    metavar="SHELL",
    help="Shell predicate: exit 0 = happened, 1 = not yet, >1 = broken.",
)
@click.option(
    "--poll", type=float, default=5.0, show_default=True, metavar="SEC", help="Probe interval."
)
@click.option(
    "--timeout",
    type=float,
    default=0.0,
    show_default=True,
    metavar="SEC",
    help="Give up after SEC seconds; 0 waits forever.",
)
def wait_cmd(until_cmd: str, poll: float, timeout: float) -> None:
    """Block until an event happens, then continue in the same session.

    Exit 0 = happened, 124 = timed out, 2 = the predicate itself is broken.
    """
    started = time.monotonic()
    deadline = None if timeout <= 0 else started + timeout
    polls = 0

    while True:
        polls += 1
        code, stderr = _probe(until_cmd)

        if code == _TRUE:
            elapsed = time.monotonic() - started
            click.echo(f"happened at {_now_stamp()} after {elapsed:.1f}s ({polls} polls)")
            sys.exit(EXIT_HAPPENED)

        if code != _NOT_YET:
            click.echo(
                f"predicate broken (exit {code}), not waiting further: {until_cmd}",
                err=True,
            )
            if stderr.strip():
                click.echo(stderr.strip()[-500:], err=True)
            sys.exit(EXIT_PREDICATE_BROKEN)

        now = time.monotonic()
        if deadline is not None and now >= deadline:
            click.echo(
                f"timeout after {now - started:.1f}s ({polls} polls), event did not happen",
                err=True,
            )
            sys.exit(EXIT_TIMEOUT)

        nap = poll if deadline is None else min(poll, deadline - now)
        time.sleep(max(nap, 0.0))
