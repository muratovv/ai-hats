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
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

from ai_hats_observe.artifacts import EVENT_LOG_JSONL
from ai_hats_observe.canonical import (
    AskKind,
    GateDecision,
    GatePoint,
    GateVerdict,
    Notice,
    PersonAsked,
    ToolResultReceived,
    WorthRecording,
)
from ai_hats_observe.canonical.types import PromptId, ToolCallId, now
from ai_hats_observe.commands import Answer, Interrupt, Prompt, Rejected, decode_command
from ai_hats_observe.event_log import read_events

from ..env import ENV_QUESTIONS_ON_WIRE
from ..wrap_runner import WrapRunner
from .copier import LogCopier
from .header import SessionHeader

if TYPE_CHECKING:
    from ai_hats_observe import Session
    from ai_hats_observe.event_log_writer import EventLogWriter

    from ..surfaces import Control, Question, Wire

#: How long the child's group gets after SIGTERM before SIGKILL. Measured claude
#: leaves in under a second; this only bounds a stuck one.
GRACE_S = 5.0
_SOURCE = "headless"
# Denied at EOF so the model stops instead of retrying into a closed session (ADR-0038 D3).
#: How long EOF waits for a question a gate recorded but claude has not yet put on the wire.
EOF_WAIT_S = 2.0
_ENDING = "The session is ending now. Do not retry this or any other tool; reply in one sentence."


class HeadlessRunner(WrapRunner):
    """``ai-hats headless`` — a HITL session driven over this process's stdin/stdout."""

    follows_main_record = False  # the wire is the main agent's record here (ADR-0038 D4)
    refuses_unready = True  # no terminal to run the surface's login flow in

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

    def _serving_hooks(self, provider, session, env: dict[str, str]):
        # the hook server and the binary share this env: both learn questions come over the wire
        env[ENV_QUESTIONS_ON_WIRE] = "1"
        return super()._serving_hooks(provider, session, env)

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

        relay = _Relay(child, self._wire(), event_log=event_log, report=session.log_sys, log=log)
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
    holder that queued in its stead would change that (ADR-0038 D3). A question
    the binary puts waits here for its answer; the first answer closes it.
    """

    def __init__(
        self,
        child: subprocess.Popen[bytes],
        wire: Wire,
        *,
        event_log: EventLogWriter,
        report: Callable[[str], None],
        log: Path,
        eof_wait: float = EOF_WAIT_S,
    ) -> None:
        self._child = child
        self._wire = wire
        self._event_log = event_log
        self._report = report
        self._log = log
        self._eof_wait = eof_wait
        self._ids: set[PromptId] = set()
        self._lock = threading.Lock()  # the questions, shared by both pumps
        self._open: dict[ToolCallId, Question] = {}
        self._closed: set[ToolCallId] = set()
        # answered before its question reached the wire (a gate records it first);
        # the flag says whether the answer is the client's or the holder's own deny
        self._early: dict[ToolCallId, tuple[Answer, str, bool]] = {}
        self._arrived = threading.Condition(self._lock)
        self._replying = 0  # held answers taken off _early and not yet written
        self._asked: set[ToolCallId] = set()  # a PersonAsked this relay passed on

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
                elif isinstance(command, Answer):
                    self._answer(command, where)
                elif isinstance(command, Interrupt):
                    self._send(self._wire.encode(command), where, "interrupt")
                else:
                    self._prompt(command, where)
        finally:
            self._deny_open()
            self._deny_announced()
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
        self._send(self._wire.encode(prompt), where, "prompt")

    def _answer(self, answer: Answer, where: str) -> None:
        announced, resolved = self._in_log(answer.call_id)
        with self._lock:
            question = self._open.pop(answer.call_id, None)
            closed = answer.call_id in self._closed or (question is None and resolved)
            hold = question is None and not closed and announced
            if hold:
                self._early[answer.call_id] = (answer, where, True)
            if question is not None or hold:
                self._closed.add(answer.call_id)
        if question is not None:
            self._decide(question, answer, where, by_person=True)
        elif not hold:
            why = "is already closed" if closed else "names no open question"
            self._reject(where, "answer", f'"call_id" {answer.call_id} {why}')

    def _decide(self, question: Question, answer: Answer, where: str, *, by_person: bool) -> None:
        if answer.answers is not None and not question.takes_answers:
            self._reject(
                where, "answer", f'"call_id" {answer.call_id} is a question that takes no answers'
            )
            with self._lock:
                self._closed.discard(question.call_id)
                self._open[question.call_id] = question
            return
        if by_person and answer.decision == "deny":
            # the wire carries only the words; logged before the reply, so it precedes the result
            refusal = GateVerdict(
                point=GatePoint.BEFORE_TOOL,
                decision=GateDecision.DENY,
                hook="person",
                reason=answer.message or "",
                tool=question.tool,
                call_id=question.call_id,
                source=_SOURCE,
                ts=now(),
            )
            self._emit(lambda: [refusal], f"the refusal of {question.call_id}")
        self._send(self._wire.reply(question, answer), where, "answer")

    def _deny_open(self) -> None:
        with self._lock:
            questions, self._open = list(self._open.values()), {}
            self._closed.update(q.call_id for q in questions)
        for question in questions:
            deny = Answer(question.call_id, "deny", _ENDING)
            self._decide(question, deny, "the end of stdin", by_person=False)

    def _deny_announced(self) -> None:
        """At EOF, deny too what a gate recorded and claude has not yet put on the wire."""
        pending = self._open_in_log()
        with self._lock:
            for call_id in pending - self._closed - set(self._open):
                deny = Answer(call_id, "deny", _ENDING)
                self._early[call_id] = (deny, "the end of stdin", False)
                self._closed.add(call_id)
            if self._early or self._replying:
                self._arrived.wait_for(
                    lambda: not self._early and not self._replying, timeout=self._eof_wait
                )
            left, self._early = dict(self._early), {}
        for call_id, (_, where, from_client) in left.items():
            if from_client:
                why = "never reached the holder before stdin closed"
                self._reject(where, "answer", f'the question on "call_id" {call_id} {why}')

    def _send(self, line: bytes, where: str, cmd: str) -> None:
        try:
            self._child.stdin.write(line)
            self._child.stdin.flush()
        except (OSError, ValueError):
            self._reject(where, cmd, "the session is ending")

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
                self._drift("malformed-json", f"{raw[:120]!r}")
                continue
            control = decoder.control(message)
            if control is not None:
                self._on_control(control)
                continue
            self._emit(lambda: decoder.decode(message), f"{raw[:120]!r}")
        self._emit(decoder.close, "the end of the surface's stdout")

    def _on_control(self, control: Control) -> None:
        from ..surfaces import Withdrawn

        if isinstance(control, Withdrawn):
            with self._lock:
                for call_id, question in list(self._open.items()):
                    if question.request_id == control.request_id:
                        del self._open[call_id]
                        self._closed.add(call_id)
            return
        with self._lock:
            held = self._early.pop(control.call_id, None)
            if held is None:
                self._open[control.call_id] = control
            else:
                self._replying += 1
        if held is not None:
            answer, where, from_client = held
            try:
                self._decide(control, answer, where, by_person=from_client)
            finally:
                with self._lock:
                    self._replying -= 1
                    self._arrived.notify_all()  # EOF waits for the write, not for the pop
            return
        if control.call_id in self._asked or self._in_log(control.call_id)[0]:
            return  # one fact, one producer: a gate records its own question
        asked = PersonAsked(
            kind=AskKind.PERMISSION,
            call_id=control.call_id,
            tool=control.tool,
            detail=control.reason,
            source=control.source,
            ts=now(),
        )
        self._emit(lambda: [asked], f"the question on {control.call_id}")

    def _in_log(self, call_id: ToolCallId) -> tuple[bool, bool]:
        """Whether the log holds a question on ``call_id``, and whether its call has a result."""
        announced, resolved = call_id in self._asked, False
        for event in read_events(self._log):
            if isinstance(event, PersonAsked) and event.call_id == call_id:
                announced = True
            elif isinstance(event, ToolResultReceived) and event.call_id == call_id:
                resolved = True
        return announced, resolved

    def _open_in_log(self) -> set[ToolCallId]:
        asked: set[ToolCallId] = set()
        answered: set[ToolCallId] = set()
        for event in read_events(self._log):
            if isinstance(event, PersonAsked) and event.call_id:
                asked.add(event.call_id)
            elif isinstance(event, ToolResultReceived):
                answered.add(event.call_id)
        return asked - answered

    def _emit(self, decode: Callable[[], list], what: str) -> None:
        # One bad line must not end the pump: every later event, turn ends included, rides it.
        try:
            events = decode()
        except Exception as exc:
            self._report(f"headless: could not read {what}: {type(exc).__name__}: {exc}")
            self._drift("decoder-error", f"{what}: {type(exc).__name__}: {exc}")
            return
        self._asked.update(e.call_id for e in events if isinstance(e, PersonAsked) and e.call_id)
        self._event_log.emit(events)

    def _drift(self, raw_code: str, detail: str) -> None:
        notice = Notice(
            reason=WorthRecording.UNSUPPORTED_RECORD,
            raw_code=raw_code,
            detail=detail,
            source=_SOURCE,
            ts=now(),
        )
        self._event_log.emit([notice])


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
