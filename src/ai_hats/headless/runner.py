"""The holder: a ``WrapRunner`` whose surface talks over pipes, not a PTY (ADR-0038 D5).

Planning, start-up checks, the resident hook server, the event log and the
finalize are ``WrapRunner``'s, unchanged. What differs is the spawn — pipes, the
child in its own process group — and that nothing reads the person's keyboard:
stdin carries commands, stdout the header and a copy of the log.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Callable, Iterator

from ai_hats_observe.artifacts import EVENT_LOG_JSONL
from ai_hats_observe.canonical import Notice, WorthRecording
from ai_hats_observe.canonical.types import PromptId, now
from ai_hats_observe.commands import Prompt, Rejected, decode_command

from ..wrap_runner import WrapRunner
from .copier import LogCopier
from .header import SessionHeader

if TYPE_CHECKING:
    from ai_hats_observe import Session
    from ai_hats_observe.event_log_writer import EventLogWriter

    from ..surfaces import Wire

#: How long the child's group gets after SIGTERM before SIGKILL. Measured claude
#: leaves in under a second; this only bounds a stuck one.
GRACE_S = 5.0
_SOURCE = "headless"


class HeadlessRunner(WrapRunner):
    """``ai-hats headless`` — a HITL session driven over this process's stdin/stdout."""

    follows_main_record = False  # the wire is the main agent's record here (ADR-0038 D4)

    def __init__(
        self,
        *args,
        stdout_fd: int,
        first_prompt: str = "",
        stdin_fd: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._stdout_fd = stdout_fd
        self._first_prompt = first_prompt
        self._stdin_fd = stdin_fd
        self._copier: LogCopier | None = None
        self._child: subprocess.Popen[bytes] | None = None
        self._signal: int | None = None

    def _wire(self) -> Wire:
        wire = self.payload.provider.wire()
        if wire is None:
            raise RuntimeError(f"ai-hats headless cannot drive {self.payload.provider.name}")
        return wire

    def run(self, extra_args=None, tags=None, pty_tap_factory=None):
        del pty_tap_factory
        args = [*self._wire().launch_args, *(extra_args or ())]
        previous = {
            sig: signal.signal(sig, self._on_signal) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            return super().run(extra_args=args, tags=tags)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            # Holder leaves last: stdout closes only after the finalize, so the
            # client's EOF means the session is over and recorded.
            if self._copier is not None:
                self._copier.finish()
            else:
                with contextlib.suppress(OSError):
                    os.close(self._stdout_fd)

    def _stdin_is_terminal(self) -> bool:
        return False  # stdin carries commands; the hold must never read it

    def _on_signal(self, signum: int, _frame) -> None:
        if self._signal is None:
            self._signal = signum
        self._terminate_child()

    def _terminate_child(self, sig: int = signal.SIGTERM) -> None:
        child = self._child
        if child is not None and child.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, sig)

    def _spawn_surface(
        self,
        cmd: list[str],
        env: dict[str, str],
        tracer,
        *,
        session: Session,
        event_log: EventLogWriter | None,
        provider_session_id: str,
        pty_tap_factory=None,
        on_spawn: Callable[[int], None],
    ) -> int:
        del tracer, pty_tap_factory
        if self._signal is not None:
            return 128 + self._signal  # stopped before there was anything to stop
        if event_log is None:
            # The wire is the log's only source here: without a writer, stdout would be empty.
            session.log_sys("headless: the session has no event log; the surface is not started")
            sys.stderr.write("ai-hats headless: this session has no event log — not starting\n")
            return 1
        log = session.session_dir / EVENT_LOG_JSONL
        child = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        self._child = child
        on_spawn(child.pid)
        header = SessionHeader(
            session_id=session.session_id,
            session_dir=session.session_dir,
            log=log,
            holder_pid=os.getpid(),
            provider_session_id=provider_session_id,
            started_at=now(),
        )
        try:
            _write_all(self._stdout_fd, header.line())
        except BrokenPipeError:
            session.log_sys("headless stdout reader went away before the header")
        sys.stderr.write(header.human())
        sys.stderr.flush()
        self._copier = LogCopier(log, self._stdout_fd, report=session.log_sys).start()

        relay = _Relay(child, self._wire(), event_log=event_log, report=session.log_sys)
        follower = threading.Thread(target=relay.follow, name="headless-follow", daemon=True)
        feeder = threading.Thread(
            target=relay.feed,
            args=(self._stdin_fd, self._first_prompt),
            name="headless-feed",
            daemon=True,
        )
        follower.start()
        feeder.start()
        code = self._wait(child)
        # Every `result` the child printed has reached the log before run_ended;
        # a pump still alive after the grace is refused by the closed writer.
        follower.join(GRACE_S)
        return code

    def _wait(self, child: subprocess.Popen[bytes]) -> int:
        deadline: float | None = None
        while True:
            try:
                code = child.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if self._signal is None:
                    continue
                if deadline is None:
                    self._terminate_child()  # the signal may have beaten the spawn
                    deadline = time.monotonic() + GRACE_S
                elif time.monotonic() >= deadline:
                    self._terminate_child(signal.SIGKILL)
        if self._signal is not None:
            return 128 + self._signal
        return code if code >= 0 else 128 - code


class _Relay:
    """The two pumps between the holder's pipes and the child's.

    A prompt goes to the wire the moment it is read: whether it waits for the
    running turn or joins it is the surface's call — claude folds it in — and a
    holder that queued in its stead would change that (ADR-0038 D3).
    """

    def __init__(
        self,
        child: subprocess.Popen[bytes],
        wire: Wire,
        *,
        event_log: EventLogWriter,
        report: Callable[[str], None],
    ) -> None:
        self._child = child
        self._wire = wire
        self._event_log = event_log
        self._report = report
        self._ids: set[PromptId] = set()

    def feed(self, stdin_fd: int, first_prompt: str) -> None:
        """Holder stdin → commands → child stdin; EOF closes the child's stdin."""
        try:
            if first_prompt:
                self._prompt(Prompt(first_prompt), "the positional prompt")
            for number, raw in enumerate(_lines(stdin_fd), start=1):
                command = decode_command(raw)
                if command is None:
                    continue
                where = f"stdin line {number}"
                if isinstance(command, Rejected):
                    self._reject(where, command.cmd, command.why)
                else:
                    self._prompt(command, where)
        finally:
            # EOF for the child: it finishes the turns it has and exits.
            with contextlib.suppress(OSError, ValueError):
                self._child.stdin.close()

    def _prompt(self, prompt: Prompt, where: str) -> None:
        if prompt.id is None:
            prompt = replace(prompt, id=PromptId(str(uuid.uuid4())))
        elif prompt.id in self._ids:
            # claude drops a repeated uuid silently yet echoes it: its turn would never end
            self._reject(where, "prompt", f'"id" {prompt.id} is already used in this session')
            return
        self._ids.add(prompt.id)
        self._send(self._wire.encode(prompt), where)

    def _send(self, line: bytes, where: str) -> None:
        try:
            self._child.stdin.write(line)
            self._child.stdin.flush()
        except (OSError, ValueError):
            self._reject(where, "prompt", "the session is ending")

    def _reject(self, where: str, cmd: str | None, why: str) -> None:
        detail = f"{where}: {why}"
        self._event_log.emit(
            [
                Notice(
                    reason=WorthRecording.COMMAND_REJECTED,
                    raw_code=cmd,
                    detail=detail,
                    source=_SOURCE,
                    ts=now(),
                )
            ]
        )

    def follow(self) -> None:
        """Child stdout → the main agent's events, in the wire's order (ADR-0038 D4)."""
        decoder = self._wire.decoder()
        for raw in iter(self._child.stdout.readline, b""):
            try:
                message = json.loads(raw)
            except ValueError:
                self._report(f"headless: a non-JSON line from the surface: {raw[:120]!r}")
                continue
            if not isinstance(message, dict):
                continue
            self._emit(lambda: decoder.decode(message), f"{raw[:120]!r}")
        self._emit(decoder.close, "the end of the surface's stdout")

    def _emit(self, decode: Callable[[], list], what: str) -> None:
        # One bad line must not end the pump: every later event, turn ends included, rides it.
        try:
            events = decode()
        except Exception as exc:
            self._report(f"headless: could not read {what}: {type(exc).__name__}: {exc}")
            return
        self._event_log.emit(events)


def _lines(fd: int) -> Iterator[bytes]:
    """Lines from a raw fd. Not ``sys.stdin``: a daemon thread blocked in its
    buffered read holds a lock the interpreter needs at exit, and aborts it."""
    pending = b""
    while chunk := os.read(fd, 65536):
        pending += chunk
        *whole, pending = pending.split(b"\n")
        for line in whole:
            yield line + b"\n"
    if pending:
        yield pending


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


__all__ = ["GRACE_S", "HeadlessRunner"]
