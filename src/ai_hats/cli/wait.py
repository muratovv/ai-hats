"""``ai-hats wait`` — block the live session until an event happens (HATS-986).

The three exit codes are the point. A shell ``until`` loop cannot tell "not true
yet" from "predicate is broken", so a typo'd predicate waits forever and the
silence reads exactly like a successful wait.
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import click

EXIT_HAPPENED = 0
EXIT_PREDICATE_BROKEN = 2
EXIT_TIMEOUT = 124

_TRUE = 0
_NOT_YET = 1


class _Broken(Exception):
    """The predicate cannot answer at all — abort instead of waiting forever."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _shell_predicate(command: str) -> Callable[[], bool]:
    def probe() -> bool:
        # shell=True is the interface, not an oversight: --until-cmd IS a shell snippet.
        done = subprocess.run(  # noqa: S602
            command, shell=True, capture_output=True, text=True
        )
        if done.returncode == _TRUE:
            return True
        if done.returncode == _NOT_YET:
            return False
        raise _Broken(f"predicate exited {done.returncode}: {command}", done.stderr)

    return probe


def _task_predicate(task_id: str, states: tuple[str, ...]) -> Callable[[], bool]:
    """Predicate over a backlog card's state.

    The integrator may import the rack, never the reverse — deferred here to keep
    ``--until-cmd`` usable with no backlog resolvable at all.
    """
    from ai_hats_rack import NoProjectRootError, TaskCard, resolve_root

    try:
        root = resolve_root(Path.cwd())
    except NoProjectRootError as exc:
        raise _Broken(f"no ai-hats project resolvable from {Path.cwd()}: {exc}") from exc

    card_path = root.tasks_dir / task_id / "task.yaml"

    def probe() -> bool:
        if not card_path.exists():
            raise _Broken(f"no such task: {task_id} (looked for {card_path})")
        try:
            card = TaskCard.from_yaml(card_path)
        except Exception as exc:
            raise _Broken(f"card {task_id} did not load: {exc!r}") from exc
        return card.state in states

    return probe


def _build_predicate(
    until_cmd: str | None, task: str | None, until: tuple[str, ...]
) -> Callable[[], bool]:
    if until_cmd and task:
        raise click.UsageError("--until-cmd and --task are alternatives, not a pair.")
    if until_cmd:
        if until:
            raise click.UsageError("--until names card states; it belongs with --task.")
        return _shell_predicate(until_cmd)
    if task:
        if not until:
            raise click.UsageError("--task needs at least one --until <state>.")
        return _task_predicate(task, until)
    raise click.UsageError("Nothing to wait for: pass --until-cmd or --task/--until.")


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@click.command("wait")
@click.option(
    "--until-cmd",
    "until_cmd",
    default=None,
    metavar="SHELL",
    help="Shell predicate: exit 0 = happened, 1 = not yet, >1 = broken.",
)
@click.option("--task", default=None, metavar="ID", help="Wait on a backlog card's state.")
@click.option(
    "--until",
    multiple=True,
    metavar="STATE",
    help="Target state for --task; repeat to accept any of several.",
)
@click.option(
    "--poll",
    type=float,
    default=5.0,
    show_default=True,
    metavar="SEC",
    help="Probe interval. Must be greater than 0.",
)
@click.option(
    "--timeout",
    type=float,
    default=0.0,
    show_default=True,
    metavar="SEC",
    help="Give up after SEC seconds; 0 waits forever. Negative values are rejected.",
)
def wait_cmd(
    until_cmd: str | None, task: str | None, until: tuple[str, ...], poll: float, timeout: float
) -> None:
    """Block until an event happens, then continue in the same session.

    Exit 0 = happened, 124 = timed out, 2 = the predicate itself is broken.
    """
    if not math.isfinite(poll) or poll <= 0:
        raise click.UsageError(f"--poll must be positive, got {poll!r}.")
    if not math.isfinite(timeout) or timeout < 0:
        raise click.UsageError(f"--timeout must be 0 (wait forever) or positive, got {timeout!r}.")

    try:
        probe = _build_predicate(until_cmd, task, until)
    except _Broken as broken:
        _abort(broken)

    started = time.monotonic()
    deadline = None if timeout <= 0 else started + timeout
    polls = 0

    while True:
        polls += 1
        try:
            happened = probe()
        except _Broken as broken:
            _abort(broken)

        if happened:
            elapsed = time.monotonic() - started
            click.echo(f"happened at {_now_stamp()} after {elapsed:.1f}s ({polls} polls)")
            sys.exit(EXIT_HAPPENED)

        now = time.monotonic()
        if deadline is not None and now >= deadline:
            click.echo(
                f"timeout after {now - started:.1f}s ({polls} polls), event did not happen",
                err=True,
            )
            sys.exit(EXIT_TIMEOUT)

        nap = poll if deadline is None else min(poll, deadline - now)
        time.sleep(max(nap, 0.0))


def _abort(broken: _Broken) -> None:
    click.echo(f"not waiting further, {broken.reason}", err=True)
    if broken.detail.strip():
        click.echo(broken.detail.strip()[-500:], err=True)
    sys.exit(EXIT_PREDICATE_BROKEN)
