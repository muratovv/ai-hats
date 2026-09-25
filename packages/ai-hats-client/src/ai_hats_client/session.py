"""A client of ``ai-hats headless`` that knows nothing but its contract (ADR-0038).

Stdlib only, no ``ai_hats`` import: what it needs is argv, one JSON command per
line on the holder's stdin, the ``headless/v1`` header and ``events/v1`` lines on
its stdout, and the exit code. That it is enough to drive a session is the point.

    with HeadlessSession.start(["ai-hats", "headless", "-r", "maintainer"]) as s:
        first = s.turn("remember the word KIWI")
        second = s.turn("which word?")
        end = s.close()
    assert "KIWI" in second.text and end.code == 0

Every wait is bounded, and every failure carries what was read so far and the
tail of the holder's stderr — the two things a failing test needs to be read.
"""  # comment-length: allow — the module is a public client; this is its manual

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HEADLESS_V1 = "headless/v1"
COMMANDS_V1 = "commands/v1"

#: One ``events/v1`` line, decoded. Its kind is ``event["event"]``.
Event = dict[str, Any]

#: Called with each ``person_asked`` a turn wait reads; it answers through ``answer()``.
OnQuestion = Callable[[Event], None]

_EOF = object()


class HeadlessError(Exception):
    """Something the client could not do; carries what it had read."""

    def __init__(self, message: str, *, events: Sequence[Event] = (), stderr: str = "") -> None:
        tail = stderr[-2000:]
        super().__init__(f"{message}\n--- holder stderr (tail) ---\n{tail}" if tail else message)
        self.events = tuple(events)
        self.stderr = stderr


class ProtocolError(HeadlessError):
    """The holder's stdout broke ``headless/v1``: no header, or a line that is not JSON."""


class HeadlessTimeout(HeadlessError):
    """A bounded wait ran out — for the header, a turn, or the end."""


class QuestionPending(HeadlessError):
    """A turn wait read a question and was given no handler to answer it with.
    Answer ``question["call_id"]`` and wait again: nothing read is lost."""

    def __init__(self, question: Event, *, events: Sequence[Event] = (), stderr: str = "") -> None:
        super().__init__(
            f"the session asks about {question.get('tool')} ({question.get('call_id')}): "
            "answer it, or pass on_question to the wait",
            events=events,
            stderr=stderr,
        )
        self.question = question


class SessionEnded(HeadlessError):
    """stdout closed while the client still waited: the session is over. ``exit``
    says how — a refusal before the start is exit 2 and no header."""

    def __init__(self, message: str, *, exit: Exit) -> None:
        super().__init__(message, events=exit.events, stderr=exit.stderr)
        self.exit = exit


@dataclass(frozen=True)
class Header:
    """Line 1 of the holder's stdout: who the session is and where its log lies."""

    session_id: str
    session_dir: Path
    log: Path
    holder_pid: int
    provider_session_id: str
    started_at: str
    events: str
    commands: tuple[str, ...]

    @classmethod
    def parse(cls, line: bytes) -> Header:
        try:
            body = json.loads(line)
        except ValueError as exc:
            raise ProtocolError(f"line 1 is not the session header: {line[:200]!r}") from exc
        if not isinstance(body, dict) or body.get("v") != HEADLESS_V1:
            raise ProtocolError(f"line 1 is not a {HEADLESS_V1} header: {line[:200]!r}")
        return cls(
            session_id=body["session_id"],
            session_dir=Path(body["session_dir"]),
            log=Path(body["log"]),
            holder_pid=int(body["holder_pid"]),
            provider_session_id=body["provider_session_id"],
            started_at=body["started_at"],
            events=body["events"],
            commands=tuple(body["commands"]),
        )


class _Events:
    events: tuple[Event, ...]

    def of(self, kind: str) -> tuple[Event, ...]:
        """The events of one kind (``"response_ended"``, ``"tool_result_received"`` …)."""
        return tuple(e for e in self.events if e.get("event") == kind)

    def signals(self, kind: str) -> tuple[Event, ...]:
        """The signals of one kind (``"command_rejected"``, ``"approaching_limit"`` …)."""
        return tuple(e for e in self.of("signal") if e.get("kind") == kind)


@dataclass(frozen=True)
class Turn(_Events):
    """Everything read since the previous turn ended, this ``turn_ended`` last."""

    events: tuple[Event, ...]

    @property
    def ended(self) -> Event:
        return self.events[-1]

    @property
    def ok(self) -> bool:
        return bool(self.ended.get("ok"))

    @property
    def prompt_ids(self) -> tuple[str, ...]:
        """The prompts this turn answered — none for a turn the surface began itself."""
        return tuple(self.ended.get("prompt_ids") or ())

    @property
    def text(self) -> str:
        """What the model said this turn: its text items, in order."""
        return "\n".join(
            e["item"]["text"]
            for e in self.of("item_emitted")
            if e.get("item", {}).get("kind") == "text"
        )


@dataclass(frozen=True)
class Exit(_Events):
    """How the session ended, once stdout closed and the holder was reaped."""

    code: int
    events: tuple[Event, ...]
    stderr: str


class HeadlessSession:
    """One running ``ai-hats headless``, driven through its pipes."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:
            raise ValueError("the holder's stdin, stdout and stderr must all be pipes")
        self._proc = proc
        self._stdin, self._stdout, self._stderr_pipe = proc.stdin, proc.stdout, proc.stderr
        self._lines: queue.Queue[object] = queue.Queue()
        self._stderr: list[bytes] = []
        self._read: list[Event] = []  # every event read, in order
        self._pending: list[Event] = []  # read, not yet handed out in a Turn
        self._turns: dict[str, Turn] = {}  # every turn read, by the prompt ids it answered
        self._handled: set[str] = set()  # call_ids a handler returned from, or answered
        self._unhandled: dict[str, Event] = {}  # questions read and not yet dealt with
        self._close_timeout = 60.0
        self._exit: Exit | None = None
        self._header: Header | None = None
        self._stdout_closed = False
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        self._stderr_pump = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stderr_pump.start()

    # -- start and the header --------------------------------------------------

    @classmethod
    def start(
        cls,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        close_timeout: float = 60.0,
    ) -> HeadlessSession:
        """Launch the holder and read its header; ``SessionEnded`` when it refused.
        ``close_timeout`` bounds how long leaving a ``with`` block waits for the end."""
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        session = cls(proc)
        session._close_timeout = close_timeout
        first = session._next_line(timeout, "the session header")
        if first is _EOF:
            end = session._reap(timeout)
            raise SessionEnded(f"the session ended before its header (exit {end.code})", exit=end)
        session._header = Header.parse(first)  # type: ignore[arg-type]
        return session

    @property
    def header(self) -> Header:
        if self._header is None:
            raise HeadlessError("the session header has not been read")
        return self._header

    @property
    def events(self) -> tuple[Event, ...]:
        """Every event read so far."""
        return tuple(self._read)

    # -- commands ---------------------------------------------------------------

    def prompt(self, text: str, id: str | None = None) -> str:
        """Send one prompt and return its id; sent mid-turn, claude may fold it
        into the running turn, whose ``turn_ended`` then lists both ids."""
        id = id or str(uuid.uuid4())
        self._send("prompt", prompt_command(text, id))
        return id

    def answer(
        self,
        call_id: str,
        decision: str,
        *,
        message: str | None = None,
        answers: Mapping[str, str] | None = None,
    ) -> None:
        """Decide the question ``call_id`` names: ``"allow"`` runs the call as it
        was asked, ``"deny"`` refuses it with ``message`` for the model. A question
        the model asked itself takes ``answers``, each question's text mapped to
        the answer. The first answer on a call_id wins; the holder refuses the rest."""
        self._send("answer", answer_command(call_id, decision, message=message, answers=answers))
        self._handled.add(call_id)
        self._unhandled.pop(call_id, None)

    def tool_call(self, call_id: str) -> dict[str, Any] | None:
        """The call a question is about, as read so far: ``name`` and ``input`` —
        for ``AskUserQuestion``, the questions and their options."""
        for event in self._read:
            item = event.get("item") or {}
            if event.get("event") == "item_emitted" and item.get("call_id") == call_id:
                return item
        return None

    def interrupt(self) -> None:
        """Stop the running turn and keep the session: it still ends with its
        ``turn_ended``, and a question it had open is closed. With no turn
        running, nothing happens."""
        self._send("interrupt", interrupt_command())

    def send_raw(self, line: str) -> None:
        """Write one line to the holder's stdin as it is — for testing its refusals.
        Safe from another thread: one write of one line."""
        try:
            self._stdin.write(line.encode("utf-8") + b"\n")
            self._stdin.flush()
        except (OSError, ValueError) as exc:
            raise HeadlessError(
                f"the session takes no more input: {exc}",
                events=self._read,
                stderr=self._stderr_text(),
            ) from exc

    def _send(self, cmd: str, line: str) -> None:
        if cmd not in self.header.commands:
            known = ", ".join(self.header.commands)
            raise HeadlessError(f"this holder does not run {cmd!r}; it runs: {known}")
        self.send_raw(line)

    # -- turns ------------------------------------------------------------------

    def next_turn(self, timeout: float = 60.0, *, on_question: OnQuestion | None = None) -> Turn:
        """Wait for the next ``turn_ended`` and return the turn it closes.

        A ``person_asked`` read on the way goes to ``on_question`` once per
        call_id; with no handler the wait raises ``QuestionPending`` rather than
        run out its bound on a question nobody will answer."""
        deadline = time.monotonic() + timeout
        while True:
            if self._unhandled and self._lines.empty():
                # offered once everything already read is in: a result may close one first
                self._offer(on_question)
            line = self._next_line(deadline - time.monotonic(), "the end of a turn")
            if line is _EOF:
                end = self._reap(timeout)
                raise SessionEnded(f"the session ended mid-turn (exit {end.code})", exit=end)
            event = self._decode(line)  # type: ignore[arg-type]
            self._pending.append(event)
            kind = event.get("event")
            if kind == "person_asked" and event.get("call_id"):
                if event["call_id"] not in self._handled:
                    self._unhandled.setdefault(event["call_id"], event)
            elif kind == "tool_result_received":
                self._unhandled.pop(event.get("call_id"), None)  # its question is closed
            if kind == "turn_ended":
                turn, self._pending = Turn(tuple(self._pending)), []
                for answered in turn.prompt_ids:
                    self._turns[answered] = turn
                return turn

    def turn_for(
        self, id: str, timeout: float = 60.0, *, on_question: OnQuestion | None = None
    ) -> Turn:
        """Wait for the turn that answered prompt ``id`` — already read, when a
        fold ended it together with an earlier prompt."""
        deadline = time.monotonic() + timeout
        while id not in self._turns:
            self.next_turn(deadline - time.monotonic(), on_question=on_question)
        return self._turns[id]

    def turn(
        self, text: str, timeout: float = 60.0, *, on_question: OnQuestion | None = None
    ) -> Turn:
        """Send one turn and wait for it to end."""
        return self.turn_for(self.prompt(text), timeout, on_question=on_question)

    def _offer(self, on_question: OnQuestion | None) -> None:
        """Hand every question not yet dealt with to the handler, or stop the wait on it.
        A question leaves the list once its handler returns, or once it is answered."""
        for call_id, question in list(self._unhandled.items()):
            if call_id not in self._unhandled:
                continue  # answered by an earlier handler in this loop
            if on_question is None:
                raise QuestionPending(question, events=self._read, stderr=self._stderr_text())
            on_question(question)
            self._handled.add(call_id)
            self._unhandled.pop(call_id, None)

    # -- the end ------------------------------------------------------------------

    def close(self, timeout: float = 60.0) -> Exit:
        """Say "no more input": the session finishes its turns, records, and exits."""
        if self._exit is None and not self._stdin.closed:
            self._stdin.close()
        return self._reap(timeout)

    def terminate(self, signum: int = signal.SIGTERM, timeout: float = 30.0) -> Exit:
        """Abort: signal the holder, then read to the end as ``close`` does."""
        if self._exit is None and self._proc.poll() is None:
            os.kill(self._proc.pid, signum)
        return self._reap(timeout)

    def __enter__(self) -> HeadlessSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._exit is not None:
            return
        if exc_type is None:
            try:
                self.close(self._close_timeout)
            except HeadlessTimeout:
                self._abort()  # the with block is over: nothing may keep spending turns
                raise
            return
        self._abort()

    def _abort(self) -> None:
        try:
            self.terminate()
        except HeadlessError:
            self._proc.kill()
            self._proc.wait(5)

    # -- plumbing -------------------------------------------------------------------

    def _pump_stdout(self) -> None:
        for line in iter(self._stdout.readline, b""):
            self._lines.put(line)
        self._lines.put(_EOF)

    def _pump_stderr(self) -> None:
        for chunk in iter(self._stderr_pipe.readline, b""):
            self._stderr.append(chunk)

    def _stderr_text(self) -> str:
        return b"".join(self._stderr).decode("utf-8", "replace")

    def _next_line(self, timeout: float, awaited: str) -> object:
        if self._stdout_closed:
            return _EOF
        try:
            line = self._lines.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            raise HeadlessTimeout(
                f"no {awaited} within the bound", events=self._read, stderr=self._stderr_text()
            ) from None
        if line is _EOF:
            self._stdout_closed = True
        return line

    def _decode(self, line: bytes) -> Event:
        if not line.endswith(b"\n"):
            raise ProtocolError(f"a torn line on stdout: {line[:200]!r}", events=self._read)
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise ProtocolError(
                f"a line on stdout is not JSON: {line[:200]!r}",
                events=self._read,
                stderr=self._stderr_text(),
            ) from exc
        self._read.append(event)
        return event

    def _reap(self, timeout: float) -> Exit:
        if self._exit is not None:
            return self._exit
        deadline = time.monotonic() + timeout
        while True:
            line = self._next_line(deadline - time.monotonic(), "the end of the session")
            if line is _EOF:
                break
            self._pending.append(self._decode(line))  # type: ignore[arg-type]
        try:
            code = self._proc.wait(max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired:
            raise HeadlessTimeout(
                "stdout closed but the holder did not exit",
                events=self._read,
                stderr=self._stderr_text(),
            ) from None
        self._stderr_pump.join(5.0)
        if not self._stdin.closed:
            self._stdin.close()
        self._exit = Exit(code=code, events=tuple(self._read), stderr=self._stderr_text())
        return self._exit


def prompt_command(text: str, id: str | None = None) -> str:
    """One ``prompt`` command, as this client writes it on the holder's stdin.
    ``ValueError`` for one the holder would refuse, so no wait hangs on it."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('"text" must be a non-empty string')
    if id is not None and not _canonical_uuid(id):
        raise ValueError(f'"id" must be a UUID in canonical form (lowercase, hyphenated): {id!r}')
    body = {"v": COMMANDS_V1, "cmd": "prompt", "text": text}
    if id is not None:
        body["id"] = id
    return json.dumps(body)


def _canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def interrupt_command() -> str:
    """One ``interrupt`` command, as this client writes it on the holder's stdin."""
    return json.dumps({"v": COMMANDS_V1, "cmd": "interrupt"})


def answer_command(
    call_id: str,
    decision: str,
    *,
    message: str | None = None,
    answers: Mapping[str, str] | None = None,
) -> str:
    """One ``answer`` command, as this client writes it on the holder's stdin.
    ``ValueError`` for one the holder would refuse, so no wait hangs on it."""
    if not isinstance(call_id, str) or not call_id:
        raise ValueError('"call_id" must be the call_id of a person_asked')
    if decision not in ("allow", "deny"):
        raise ValueError(f'"decision" must be "allow" or "deny", not {decision!r}')
    if message is not None and decision == "allow":
        raise ValueError('"message" goes with a deny; an allow runs the call')
    if answers is not None and not all(
        isinstance(k, str) and isinstance(v, str) for k, v in dict(answers).items()
    ):
        raise ValueError('"answers" must map each question\'s text to a string')
    body: dict[str, Any] = {"v": COMMANDS_V1, "cmd": "answer", "call_id": call_id}
    body["decision"] = decision
    if message is not None:
        body["message"] = message
    if answers is not None:
        body["answers"] = dict(answers)
    return json.dumps(body)


__all__ = [
    "Event",
    "Exit",
    "Header",
    "HeadlessError",
    "HeadlessSession",
    "HeadlessTimeout",
    "OnQuestion",
    "ProtocolError",
    "QuestionPending",
    "SessionEnded",
    "Turn",
    "answer_command",
    "interrupt_command",
    "prompt_command",
]
