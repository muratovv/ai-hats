"""``ai-hats wait`` — block the live session until an event happens (HATS-986).

The three exit codes are the point. A shell ``until`` loop cannot tell "not true
yet" from "predicate is broken", so a typo'd predicate waits forever and the
silence reads exactly like a successful wait. Every probe is bounded too
(HATS-1598): one hung probe otherwise makes ``--timeout`` unreachable.

The predicate's ``1`` means "not yet", not the ``BROKE`` of ADR-0020 D2 — a hook
has no "not yet" to express; the ADR carries the same note from its side.
"""

from __future__ import annotations

import math
import signal
import subprocess
import sys
import time
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import click

EXIT_HAPPENED = 0
EXIT_PREDICATE_BROKEN = 2
EXIT_TIMEOUT = 124

_TRUE = 0
_NOT_YET = 1

#: Default per-probe bound. A predicate is a question, not a job: 30s covers a
#: network probe and still fires long before a hung one wedges the session.
PROBE_TIMEOUT_S = 30.0


class _Broken(Exception):
    """The predicate cannot answer at all — abort instead of waiting forever."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class _ProbeExpired(Exception):
    """One probe outlived the budget it was handed.

    Which exit code that means is the caller's call, not the probe's: the loop
    picks the bound, so only it knows whether the wait's deadline or the
    per-probe budget is the one that ran out.
    """

    def __init__(self, budget: float) -> None:
        super().__init__(f"probe exceeded {budget:g}s")
        self.budget = budget


def _kill_group(running: subprocess.Popen) -> None:
    """Kill the expired probe and everything it started.

    ``kill()`` reaps the shell alone, so a compound predicate's children — the
    dead ``ssh`` the wait was actually about — survive reparented while the wait
    reports itself bounded.
    """
    try:
        os.killpg(os.getpgid(running.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        running.kill()


def _shell_predicate(command: str) -> Callable[[float | None], bool]:
    def probe(budget: float | None) -> bool:
        # shell=True is the interface, not an oversight: --until-cmd IS a shell snippet.
        with subprocess.Popen(  # noqa: S602
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Its own process group, so an expired probe can be killed whole —
            # see _kill_group.
            start_new_session=True,
        ) as running:
            try:
                _, stderr = running.communicate(timeout=budget)
            except subprocess.TimeoutExpired:
                _kill_group(running)
                raise _ProbeExpired(budget if budget is not None else 0.0) from None
            code = running.returncode

        if code == _TRUE:
            return True
        if code == _NOT_YET:
            return False
        raise _Broken(f"predicate exited {code}: {command}", stderr)

    return probe


def _task_predicate(task_id: str, states: tuple[str, ...]) -> Callable[[float | None], bool]:
    """Predicate over a backlog card's state.

    The integrator may import the rack, never the reverse — deferred here to keep
    ``--until-cmd`` usable with no backlog resolvable at all.
    """
    from ai_hats_rack import ForeignProjectPinError, NoProjectRootError, TaskCard, resolve_root

    try:
        root = resolve_root(Path.cwd(), environ=os.environ)
    except (NoProjectRootError, ForeignProjectPinError) as exc:
        raise _Broken(f"no ai-hats project resolvable from {Path.cwd()}: {exc}") from exc

    card_path = root.tasks_dir / task_id / "task.yaml"

    def probe(budget: float | None) -> bool:  # noqa: ARG001 — a card read cannot hang
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
) -> Callable[[float | None], bool]:
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


def _probe_budget(probe_timeout: float, deadline: float | None) -> tuple[float | None, bool]:
    """The next probe's bound, and whether the WAIT's deadline is what sets it.

    Both bounds apply and the nearer one wins: ``--timeout`` stays the ceiling
    under a laxer ``--probe-timeout``, and ``--probe-timeout`` still covers
    ``--timeout 0``, where there is no deadline to bound a probe with.
    """
    cap = probe_timeout if probe_timeout > 0 else None
    if deadline is None:
        return cap, False
    remaining = max(deadline - time.monotonic(), 0.0)
    if cap is None or remaining <= cap:
        return remaining, True
    return cap, False


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
@click.option(
    "--probe-timeout",
    "probe_timeout",
    type=float,
    default=PROBE_TIMEOUT_S,
    show_default=True,
    metavar="SEC",
    help="Give up on ONE probe after SEC seconds (exit 2); 0 lets a probe run unbounded.",
)
def wait_cmd(
    until_cmd: str | None,
    task: str | None,
    until: tuple[str, ...],
    poll: float,
    timeout: float,
    probe_timeout: float,
) -> None:
    """Block until an event happens, then continue in the same session.

    Exit 0 = happened, 124 = timed out, 2 = the predicate itself is broken or
    a flag value is bad (--poll <= 0, negative --timeout/--probe-timeout).
    """
    if not math.isfinite(poll) or poll <= 0:
        raise click.UsageError(f"--poll must be finite and positive, got {poll!r}.")
    if not math.isfinite(timeout) or timeout < 0:
        raise click.UsageError(
            f"--timeout must be finite and 0 (wait forever) or positive, got {timeout!r}."
        )
    if not math.isfinite(probe_timeout) or probe_timeout < 0:
        raise click.UsageError(
            f"--probe-timeout must be finite and 0 (unbounded) or positive, got {probe_timeout!r}."
        )

    try:
        probe = _build_predicate(until_cmd, task, until)
    except _Broken as broken:
        _abort(broken)

    started = time.monotonic()
    deadline = None if timeout <= 0 else started + timeout
    polls = 0

    while True:
        polls += 1
        budget, by_deadline = _probe_budget(probe_timeout, deadline)
        try:
            happened = probe(budget)
        except _Broken as broken:
            _abort(broken)
        except _ProbeExpired as expired:
            if by_deadline:
                _timed_out(started, polls, "predicate still running")
            _abort(_Broken(f"predicate did not answer in {expired.budget:g}s: {until_cmd}"))

        if happened:
            elapsed = time.monotonic() - started
            click.echo(f"happened at {_now_stamp()} after {elapsed:.1f}s ({polls} polls)")
            sys.exit(EXIT_HAPPENED)

        now = time.monotonic()
        if deadline is not None and now >= deadline:
            _timed_out(started, polls, "event did not happen")

        nap = poll if deadline is None else min(poll, deadline - now)
        time.sleep(max(nap, 0.0))


def _timed_out(started: float, polls: int, detail: str) -> NoReturn:
    """Give up on the deadline. ``detail`` says what the clock caught."""
    elapsed = time.monotonic() - started
    click.echo(f"timeout after {elapsed:.1f}s ({polls} polls), {detail}", err=True)
    sys.exit(EXIT_TIMEOUT)


def _abort(broken: _Broken) -> NoReturn:
    click.echo(f"not waiting further, {broken.reason}", err=True)
    if broken.detail.strip():
        click.echo(broken.detail.strip()[-500:], err=True)
    sys.exit(EXIT_PREDICATE_BROKEN)
