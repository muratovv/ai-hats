"""A claude stand-in that speaks the stream-json wire, for tests of a headless client.

It follows the wire measured on claude 2.1.281: NDJSON on stdin, one line per event on stdout, and a transcript in
``$CLAUDE_CONFIG_DIR/projects/<key(cwd)>/<session-id>.jsonl`` whose turn is on
disk before that turn's ``result`` line. What each turn does is chosen by its
prompt text:

    anything        answer ``ok: <text>``
    @recall         answer with the previous prompt's text
    @tool           two responses: a Bash ``tool_use``, then ``done``
    @error <msg>    an API-error record and a ``result`` with ``is_error``
    @die <code>     exit with <code> in the middle of the turn
    @sleep <s> ...  wait <s> seconds, then run the rest of the text as the turn
    @late <s> ...   write the turn's record <s> seconds after its ``result``
    @fold           a Bash ``tool_use``; a prompt already waiting at the tool
                    boundary joins this turn, as claude 2.1.281 folds one in
    @bg             answer, then start a turn of the binary's own (a finished
                    background task): no prompt, ``user_message_uuids`` empty
    @drift          a wire line of an unknown type and one that is not JSON,
                    then answer
    @quota <status> a ``rate_limit_event`` with that status; ``rejected`` is
                    the wall: then an API-error record (429) ends the turn
    @ignore-term    ignore SIGTERM from here on, then hang in the turn
    @kill <sig>     kill itself with signal <sig> in the middle of the turn
    @ask            a Bash ``tool_use`` the binary asks about (``can_use_tool``
                    under ``--permission-prompt-tool stdio``): on allow the call
                    runs and writes ``stub-ran`` in the cwd, on deny or no
                    answer it fails with the reply's message
    @askq           the model asks "Which color?" with ``AskUserQuestion``: an
                    allow with ``answers`` writes them to ``stub-answers`` and
                    the turn says the answer; without them, it asks in words
    @plan           the model asks to leave plan mode with ``ExitPlanMode``: the
                    turn says ``planned`` on allow, ``still planning`` on deny
    @slow <s>       a response that takes <s> seconds to write
    @slowtool <s>   a Bash call that runs <s> seconds
    /model <x>, /context, /usage, /rename
                    run locally, as claude 2.1.283 does: a ``<synthetic>``
                    answer, and only then the echo, in ``<command-name>`` form
    /clear          start over: ``conversation_reset``, then a new session id
                    and a new transcript for everything after it
    /tui, /login, /logout, /theme, /status, /upgrade, /voice
                    refused here: a ``<synthetic>`` "isn't available" and no
                    echo at all; any other ``/x`` is an ordinary prompt

An ``interrupt`` control request cuts the running response or tool the way
claude does — and takes back a question still open — and the next prompt is
served as usual.

A prompt whose ``uuid`` was seen before is echoed and dropped, as claude does.

Every prompt line is followed on the wire by ``command_lifecycle``: ``queued`` when
it is read, ``started`` when its turn begins (after the echo of one folded into a
running turn), ``completed`` after its ``result``.

With ``--replay-user-messages`` a turn opens with the prompt's echo, and with
``--include-partial-messages`` each response is framed by ``message_start`` /
``message_delta`` / ``message_stop`` — the final usage rides the delta, as on
the live wire.

Usage: ``stub_claude.py <tag> <claude argv...>``. The tag is the stub's own
argv[1], so a test can find the process with ``pgrep -f`` and nothing else;
``auth status`` answers logged-in, as the readiness probe asks it to — or
logged-out when ``STUB_CLAUDE_LOGGED_OUT`` is set.
"""  # comment-length: allow — the directive table is the stub's contract with its tests

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ARGV_LOG_ENV = "STUB_CLAUDE_ARGV"
TIMING_ENV = "STUB_CLAUDE_TIMING"
LOGGED_OUT_ENV = "STUB_CLAUDE_LOGGED_OUT"
#: When a quota window lifts, as epoch seconds — the one a @quota turn reports.
QUOTA_RESETS_AT = 1790340000

# What claude writes when a person stops a tool, measured on 2.1.281.
PERSON_STOP = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it "
    "was a file edit, the new_string was NOT written to the file). STOP what you are doing and "
    "wait for the user to tell you how to proceed."
)


# Slash commands claude 2.1.283 runs itself in stream-json mode, and those it refuses there.
_LOCAL_COMMANDS = frozenset({"model", "context", "usage", "rename"})
_REFUSED_COMMANDS = frozenset({"tui", "login", "logout", "theme", "status", "upgrade", "voice"})


class _Interrupted(Exception):
    """The running turn was cut; it has already said so on the wire."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _flag(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _command_name(command: str, args: str) -> str:
    """How claude writes a slash command it ran itself into the record and the echo."""
    return (
        f"<command-name>/{command}</command-name>\n"
        f"            <command-message>{command}</command-message>\n"
        f"            <command-args>{args}</command-args>"
    )


def _content(msg: dict) -> str:
    content = msg.get("message", {}).get("content", "")
    return content if isinstance(content, str) else json.dumps(content)


class Stub:
    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.session_id = _flag(argv, "--session-id") or str(uuid.uuid4())
        self.model = _flag(argv, "--model") or "stub-model"
        self.plugin_dir = _flag(argv, "--plugin-dir")
        config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
        key = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.getcwd()))
        self.transcript = Path(config) / "projects" / key / f"{self.session_id}.jsonl"
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        self.previous = ""
        self.results = 0
        self.cost = 0.0
        self.replay = "--replay-user-messages" in argv
        self.partial = "--include-partial-messages" in argv
        self.deferred: list[dict] | None = None  # a @late turn's record, held back
        self.writers: list[threading.Thread] = []
        self.inbox: queue.Queue[dict | None] = queue.Queue()
        self.seen: set[str] = set()
        self.turn_ids: list[str] = []  # the prompts the running turn answers
        self.asks = _flag(argv, "--permission-prompt-tool") == "stdio"
        self.replies: dict[str, dict] = {}  # control_response bodies, by request id
        self.stdin_closed = False
        self.cv = threading.Condition()
        self.denials: list[dict] = []  # the running turn's refused calls
        self.interrupt = threading.Event()
        self.out_lock = threading.Lock()

    # -- wire and record ---------------------------------------------------

    def emit(self, obj: dict) -> None:
        with self.out_lock:  # the stdin reader answers interrupts from its own thread
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    def record(self, obj: dict) -> None:
        obj.setdefault("sessionId", self.session_id)
        obj.setdefault("uuid", str(uuid.uuid4()))
        obj.setdefault("timestamp", _now())
        if self.deferred is not None:
            self.deferred.append(obj)
            return
        self._append(obj)

    def _append(self, obj: dict) -> None:
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(obj) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def write_late(self, records: list[dict], delay: float, result_at: float) -> None:
        """A @late turn's record lands ``delay`` after its result; the timing is
        logged so a test can prove the record really trailed the wire."""

        def later() -> None:
            time.sleep(delay)
            for obj in records:
                self._append(obj)
            log = os.environ.get(TIMING_ENV)
            if log:
                with open(log, "a", encoding="utf-8") as handle:
                    line = {"result": result_at, "record": time.time()}
                    handle.write(json.dumps(line) + "\n")

        writer = threading.Thread(target=later)
        writer.start()
        self.writers.append(writer)

    def init(self) -> None:
        plugins, skills = [], []
        if self.plugin_dir:
            root = Path(self.plugin_dir)
            plugins.append({"name": root.name, "path": str(root)})
            skills = sorted(p.name for p in (root / "skills").glob("*") if p.is_dir())
        self.emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": self.session_id,
                "permissionMode": "default",
                "model": self.model,
                "plugins": plugins,
                "skills": skills,
            }
        )

    def respond(self, request: str, blocks: list[dict], stop_reason: str) -> None:
        """One API response: in the record first, then on the wire, where the
        fragment's usage is mid-stream and the final one rides message_delta."""
        final = {"input_tokens": 10, "output_tokens": 5}
        message = {
            "id": f"msg_{request}",
            "role": "assistant",
            "model": self.model,
            "stop_reason": stop_reason,
            "usage": final,
            "content": blocks,
        }
        mid = {**message, "stop_reason": None, "usage": {**final, "output_tokens": 1}}
        record = {"type": "assistant", "requestId": request, "message": message}
        self.record(record)
        if self.partial:
            self.stream({"type": "message_start", "message": {**mid, "content": []}})
        self.emit(
            {
                "type": "assistant",
                "message": mid,
                "request_id": request,
                "uuid": record["uuid"],
                "timestamp": record["timestamp"],
                "parent_tool_use_id": None,
                "session_id": self.session_id,
            }
        )
        if self.partial:
            self.stream(
                {"type": "message_delta", "delta": {"stop_reason": stop_reason}, "usage": final}
            )
            self.stream({"type": "message_stop"})

    def stream(self, event: dict) -> None:
        self.emit(
            {
                "type": "stream_event",
                "event": event,
                "parent_tool_use_id": None,
                "session_id": self.session_id,
                "uuid": str(uuid.uuid4()),
            }
        )

    def tool_result(self, call: str, content: str, *, is_error: bool = False) -> None:
        block = {"type": "tool_result", "tool_use_id": call, "content": content}
        if is_error:
            block["is_error"] = True
        record = {"type": "user", "message": {"role": "user", "content": [block]}}
        self.record(record)
        self.emit({**record, "parent_tool_use_id": None, "session_id": self.session_id})

    def result(
        self,
        text: str,
        *,
        is_error: bool = False,
        stop_reason: str | None = "end_turn",
        subtype: str = "success",
        terminal_reason: str | None = "completed",
    ) -> None:
        self.cost += 0.001
        line = {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "stop_reason": stop_reason,
            "result": text,
            "result_index": self.results,
            "permission_denials": list(self.denials),
            "total_cost_usd": round(self.cost, 6),
            "session_id": self.session_id,
            "user_message_uuids": list(self.turn_ids),
        }
        if terminal_reason is not None:  # a local command's result has none
            line["terminal_reason"] = terminal_reason
        self.emit(line)
        self.results += 1
        self.denials = []
        for prompt_uuid in self.turn_ids:
            self.lifecycle(prompt_uuid, "completed")

    def lifecycle(self, prompt_uuid: str, state: str) -> None:
        self.emit(
            {
                "type": "command_lifecycle",
                "command_uuid": prompt_uuid,
                "state": state,
                "uuid": str(uuid.uuid4()),
                "session_id": self.session_id,
            }
        )

    def permission(self, call: str, tool: str, tool_input: dict) -> dict | None:
        """Ask on the wire and wait for the reply; ``None`` when stdin closed first."""
        if not self.asks:
            return None
        request = str(uuid.uuid4())
        self.emit(
            {
                "type": "control_request",
                "request_id": request,
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": tool,
                    "display_name": tool,
                    "input": tool_input,
                    "tool_use_id": call,
                },
            }
        )
        with self.cv:
            self.cv.wait_for(
                lambda: request in self.replies or self.stdin_closed or self.interrupt.is_set(),
                timeout=120,
            )
            reply = self.replies.pop(request, None)
        if reply is None and self.interrupt.is_set():
            self.emit({"type": "control_cancel_request", "request_id": request})
            self.cut_tool(call, tool, tool_input)
        return reply

    def marker(self, text: str) -> None:
        record = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        }
        self.record(record)
        self.emit({**record, "parent_tool_use_id": None, "session_id": self.session_id})

    def cut_tool(self, call: str, tool: str, tool_input: dict) -> None:
        """An interrupt during a tool: refused result, marker, an aborted turn."""
        self.tool_result(call, PERSON_STOP, is_error=True)
        self.denials.append({"tool_name": tool, "tool_use_id": call, "tool_input": tool_input})
        self.marker("[Request interrupted by user for tool use]")
        self.result(
            "",
            is_error=True,
            stop_reason="tool_use",
            subtype="error_during_execution",
            terminal_reason="aborted_tools",
        )
        raise _Interrupted

    def slow_response(self, request: str, seconds: float) -> None:
        """A response still being written; an interrupt cuts it mid-stream."""
        mid = {
            "id": f"msg_{request}",
            "role": "assistant",
            "model": self.model,
            "stop_reason": None,
            "usage": {"input_tokens": 10, "output_tokens": 1},
            "content": [{"type": "text", "text": "partial"}],
        }
        self.record({"type": "assistant", "requestId": request, "message": mid})
        if self.partial:
            self.stream({"type": "message_start", "message": {**mid, "content": []}})
        self.emit(
            {
                "type": "assistant",
                "message": mid,
                "request_id": request,
                "uuid": str(uuid.uuid4()),
                "timestamp": _now(),
                "parent_tool_use_id": None,
                "session_id": self.session_id,
            }
        )
        if self.interrupt.wait(seconds):
            self.marker("[Request interrupted by user]")
            self.result(
                "",
                is_error=True,
                stop_reason=None,
                subtype="error_during_execution",
                terminal_reason="aborted_streaming",
            )
            raise _Interrupted
        if self.partial:
            final = {"input_tokens": 10, "output_tokens": 5}
            self.stream(
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": final}
            )
            self.stream({"type": "message_stop"})
        self.result("finished")

    def asked_call(self, request: str, tool: str, tool_input: dict) -> dict | None:
        """A tool call the binary asks about; the reply that let it run, or ``None``."""
        call = f"toolu_{request}"
        self.respond(
            f"{request}a",
            [{"type": "tool_use", "id": call, "name": tool, "input": tool_input}],
            "tool_use",
        )
        reply = self.permission(call, tool, tool_input)
        if reply is not None and reply.get("behavior") == "allow":
            return {"call": call, **reply}
        why = (reply or {}).get("message") or "Tool permission request failed: stream closed"
        self.tool_result(call, why, is_error=True)
        self.denials.append({"tool_name": tool, "tool_use_id": call, "tool_input": tool_input})
        return None

    # -- a turn --------------------------------------------------------------

    def turn(self, text: str, prompt_uuid: str) -> None:
        self.interrupt.clear()  # an interrupt between turns stops nothing
        self.lifecycle(prompt_uuid, "started")
        command, _, args = text[1:].partition(" ") if text.startswith("/") else ("", "", "")
        if command == "clear":
            self.clear(prompt_uuid)
            return
        self.init()
        if command in _LOCAL_COMMANDS | _REFUSED_COMMANDS:
            self.slash_command(command, args, prompt_uuid)
            return
        body, late = text, None
        while body.startswith(("@sleep ", "@late ")):
            word, seconds, *rest = body.split(" ", 2)
            if word == "@sleep":
                time.sleep(float(seconds))
            else:
                late = float(seconds)
            body = rest[0] if rest else ""
        if late is not None:
            self.deferred = []
        prompt = {
            "type": "user",
            "promptSource": "sdk",
            "uuid": prompt_uuid,
            "message": {"role": "user", "content": text},
        }
        self.record(prompt)
        self.echo(text, prompt_uuid, prompt["timestamp"])
        self.turn_ids = [prompt_uuid]
        try:
            self.run(body, text)
        except _Interrupted:
            self.previous = text
        if late is not None:
            records, self.deferred = self.deferred, None
            self.write_late(records, late, time.time())

    def clear(self, prompt_uuid: str) -> None:
        """/clear: the conversation starts over under a new session id and transcript."""
        self.turn_ids = [prompt_uuid]
        self.emit(
            {
                "type": "conversation_reset",
                "new_conversation_id": str(uuid.uuid4()),
                "trigger": "clear",
                "user_message_uuid": prompt_uuid,
                "timestamp": _now(),
                "uuid": str(uuid.uuid4()),
                "session_id": self.session_id,  # still the old one, as measured
            }
        )
        self.session_id = str(uuid.uuid4())
        self.transcript = self.transcript.with_name(f"{self.session_id}.jsonl")
        self.previous = ""  # the model no longer remembers
        self.init()
        said = _command_name("clear", "")
        self.record(
            {"type": "user", "uuid": prompt_uuid, "message": {"role": "user", "content": said}}
        )
        self.echo(said, prompt_uuid, _now())
        self.result("", stop_reason=None, terminal_reason=None)

    def slash_command(self, command: str, args: str, prompt_uuid: str) -> None:
        """Answered by the binary, not the model: the answer first, the echo after it."""
        self.turn_ids = [prompt_uuid]
        if command in _REFUSED_COMMANDS:
            answer = f"/{command} isn't available in this environment."
        else:
            answer = f"Set model to {args}" if command == "model" else f"/{command} done"
        self.emit(
            {
                "type": "assistant",
                "message": {
                    "id": str(uuid.uuid4()),
                    "model": "<synthetic>",
                    "role": "assistant",
                    "stop_reason": "end_turn",
                    "type": "message",
                    "content": [{"type": "text", "text": answer}],
                },
                "parent_tool_use_id": None,
                "session_id": self.session_id,
                "uuid": str(uuid.uuid4()),
            }
        )
        if command in _LOCAL_COMMANDS:
            said = _command_name(command, args)
            self.record(
                {"type": "user", "uuid": prompt_uuid, "message": {"role": "user", "content": said}}
            )
            self.echo(said, prompt_uuid, _now())
        self.record({"type": "system", "subtype": "local_command", "content": answer})
        self.result(answer, stop_reason=None, terminal_reason=None)

    def echo(self, text: str, prompt_uuid: str, ts: str) -> None:
        if self.replay:
            self.emit(
                {
                    "type": "user",
                    "message": {"role": "user", "content": text},
                    "uuid": prompt_uuid,
                    "timestamp": ts,
                    "isReplay": True,
                    "parent_tool_use_id": None,
                    "session_id": self.session_id,
                }
            )

    def fold_waiting(self, timeout: float = 5.0) -> None:
        """Take a prompt already waiting into the running turn: its echo, its id
        in the turn's result, and in the record only a queued_command."""
        try:
            msg = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return
        if msg is None:
            self.inbox.put(None)  # EOF stays last for serve()
            return
        text, prompt_uuid = _content(msg), msg.get("uuid") or str(uuid.uuid4())
        self.seen.add(prompt_uuid)
        self.record(
            {
                "type": "attachment",
                "attachment": {
                    "type": "queued_command",
                    "prompt": text,
                    "source_uuid": prompt_uuid,
                },
            }
        )
        self.echo(text, prompt_uuid, _now())
        self.lifecycle(prompt_uuid, "started")  # a folded prompt's echo comes first
        self.turn_ids.append(prompt_uuid)

    def own_turn(self) -> None:
        """A turn the binary starts itself: the record has its notification, the wire no echo."""
        self.init()
        self.turn_ids = []
        self.record(
            {
                "type": "user",
                "promptSource": "system",
                "message": {
                    "role": "user",
                    "content": "<task-notification>done</task-notification>",
                },
            }
        )
        request = uuid.uuid4().hex[:12]
        self.respond(request, [{"type": "text", "text": "background done"}], "end_turn")
        self.result("background done")

    def run(self, body: str, text: str) -> None:
        request = uuid.uuid4().hex[:12]
        if body.startswith("@die "):
            sys.stdout.flush()
            os._exit(int(body.split()[1]))
        if body.startswith("@kill "):
            sys.stdout.flush()
            os.kill(os.getpid(), int(body.split()[1]))
            time.sleep(60)
        if body == "@ignore-term":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(120)
        if body.startswith("@quota "):
            status = body.split()[1]
            self.emit(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": status,
                        "resetsAt": QUOTA_RESETS_AT,
                        "rateLimitType": "five_hour",
                        "unifiedWindows": {
                            "five_hour": {"utilization": 0.91, "resetsAt": QUOTA_RESETS_AT}
                        },
                    },
                    "uuid": str(uuid.uuid4()),
                    "session_id": self.session_id,
                }
            )
            if status == "rejected":
                self.api_error(request, "You've hit your session limit", "rate_limit", 429)
                self.previous = text
                return
            body = "quota"
        if body.startswith("@error"):
            detail = f"API Error: {body[len('@error') :].strip() or '529 overloaded'}"
            self.api_error(request, detail, "server_error")
        elif body == "@drift":
            self.emit({"type": "future_line", "session_id": self.session_id})
            sys.stdout.write("Warning: not a JSON line\n")
            self.respond(request, [{"type": "text", "text": "ok: drift"}], "end_turn")
            self.result("ok: drift")
        elif body == "@tool":
            call = f"toolu_{request}"
            self.respond(
                f"{request}a",
                [{"type": "tool_use", "id": call, "name": "Bash", "input": {"command": "echo hi"}}],
                "tool_use",
            )
            self.tool_result(call, "hi")
            self.respond(f"{request}b", [{"type": "text", "text": "done"}], "end_turn")
            self.result("done")
        elif body == "@fold":
            call = f"toolu_{request}"
            self.respond(
                f"{request}a",
                [{"type": "tool_use", "id": call, "name": "Bash", "input": {"command": "echo hi"}}],
                "tool_use",
            )
            self.tool_result(call, "hi")
            self.fold_waiting()
            self.respond(f"{request}b", [{"type": "text", "text": "folded"}], "end_turn")
            self.result("folded")
        elif body == "@ask":
            allowed = self.asked_call(request, "Bash", {"command": "echo hi"})
            if allowed is not None:
                ran = (allowed.get("updatedInput") or {}).get("command", "")
                Path("stub-ran").write_text(ran)
                self.tool_result(allowed["call"], f"ran: {ran}")
            text = "allowed" if allowed is not None else "denied"
            self.respond(f"{request}b", [{"type": "text", "text": text}], "end_turn")
            self.result(text)
        elif body == "@askq":
            options = [
                {"label": "Red", "description": "red"},
                {"label": "Blue", "description": "blue"},
            ]
            ask = {"question": "Which color?", "header": "Color", "options": options}
            allowed = self.asked_call(
                request, "AskUserQuestion", {"questions": [{**ask, "multiSelect": False}]}
            )
            answers = ((allowed or {}).get("updatedInput") or {}).get("answers")
            if allowed is not None and answers:
                Path("stub-answers").write_text(json.dumps(answers))
                said = ", ".join(f'"{q}"="{a}"' for q, a in answers.items())
                self.tool_result(allowed["call"], f"The user answered: {said}.")
                text = next(iter(answers.values()))
            else:
                if allowed is not None:
                    self.tool_result(allowed["call"], "The user did not answer the questions.")
                text = "Which color do you prefer, red or blue?"
            self.respond(f"{request}b", [{"type": "text", "text": text}], "end_turn")
            self.result(text)
        elif body == "@plan":
            allowed = self.asked_call(request, "ExitPlanMode", {"plan": "write the file"})
            if allowed is not None:
                self.tool_result(allowed["call"], "User has approved your plan.")
            text = "planned" if allowed is not None else "still planning"
            self.respond(f"{request}b", [{"type": "text", "text": text}], "end_turn")
            self.result(text)
        elif body.startswith("@slow "):
            self.slow_response(request, float(body.split()[1]))
        elif body.startswith("@slowtool "):
            seconds = float(body.split()[1])
            call, command = f"toolu_{request}", {"command": f"sleep {seconds:g}"}
            self.respond(
                f"{request}a",
                [{"type": "tool_use", "id": call, "name": "Bash", "input": command}],
                "tool_use",
            )
            if self.interrupt.wait(seconds):
                self.cut_tool(call, "Bash", command)
            self.tool_result(call, "slept")
            self.respond(f"{request}b", [{"type": "text", "text": "done"}], "end_turn")
            self.result("done")
        elif body == "@bg":
            self.respond(request, [{"type": "text", "text": "started"}], "end_turn")
            self.result("started")
            self.own_turn()
        else:
            answer = self.previous if body == "@recall" else f"ok: {body}"
            self.respond(request, [{"type": "text", "text": answer}], "end_turn")
            self.result(answer)
        self.previous = text

    def api_error(self, request: str, detail: str, error: str, status: int | None = None) -> None:
        """A turn the API refused: a synthetic message, in the record and on the wire."""
        message = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "model": "<synthetic>",
            "content": [{"type": "text", "text": detail}],
        }
        record = {
            "type": "assistant",
            "isApiErrorMessage": True,
            "error": error,
            "requestId": request,
            "message": message,
        }
        if status is not None:
            record["apiErrorStatus"] = status
            record["quotaLimits"] = {"status": "rejected", "resetsAt": QUOTA_RESETS_AT}
        self.record(record)
        wire = {
            "type": "assistant",
            "is_api_error_message": True,
            "error": error,
            "request_id": request,
            "uuid": record["uuid"],
            "timestamp": record["timestamp"],
            "message": message,
            "parent_tool_use_id": None,
            "session_id": self.session_id,
        }
        self.emit(wire)
        self.result(detail, is_error=True, stop_reason="stop_sequence")

    def read_stdin(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"stub claude: not JSON: {line[:80]!r}", file=sys.stderr)
                continue
            if msg.get("type") == "user":
                if msg.get("uuid"):
                    self.lifecycle(msg["uuid"], "queued")
                self.inbox.put(msg)
            elif (msg.get("request") or {}).get("subtype") == "interrupt":
                self.emit(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": msg.get("request_id"),
                            "response": {"still_queued": []},
                        },
                    }
                )
                with self.cv:
                    self.interrupt.set()
                    self.cv.notify_all()
            elif msg.get("type") == "control_response":
                response = msg.get("response") or {}
                with self.cv:
                    self.replies[str(response.get("request_id"))] = response.get("response") or {}
                    self.cv.notify_all()
        with self.cv:
            self.stdin_closed = True
            self.cv.notify_all()
        self.inbox.put(None)

    def serve(self) -> int:
        threading.Thread(target=self.read_stdin, daemon=True).start()
        while (msg := self.inbox.get()) is not None:
            prompt_uuid = msg.get("uuid") or str(uuid.uuid4())
            if prompt_uuid in self.seen:
                self.echo(_content(msg), prompt_uuid, _now())  # dropped, yet echoed
                continue
            self.seen.add(prompt_uuid)
            self.turn(_content(msg), prompt_uuid)
        for writer in self.writers:
            writer.join()
        self.record({"type": "last-prompt", "lastPrompt": self.previous})
        return 0


@dataclass(frozen=True)
class StubClaude:
    """One installed stub: put :meth:`env` into the session's environment."""

    tag: str
    bin_dir: Path
    config_dir: Path
    argv_log: Path

    @property
    def timing_log(self) -> Path:
        return self.argv_log.with_name("stub-timing.ndjson")

    def env(self, path: str) -> dict[str, str]:
        return {
            "PATH": os.pathsep.join([str(self.bin_dir), path]),
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            ARGV_LOG_ENV: str(self.argv_log),
            TIMING_ENV: str(self.timing_log),
        }

    def timings(self) -> list[dict[str, float]]:
        """When each @late turn's result went out and when its record landed."""
        if not self.timing_log.exists():
            return []
        return [json.loads(line) for line in self.timing_log.read_text().splitlines() if line]

    def argvs(self) -> list[list[str]]:
        """Every argv the stub was launched with for a session (not ``auth status``)."""
        if not self.argv_log.exists():
            return []
        return [json.loads(line) for line in self.argv_log.read_text().splitlines() if line]

    def running(self) -> list[int]:
        """Pids of this stub still alive — the orphan check."""
        out = subprocess.run(["pgrep", "-f", self.tag], capture_output=True, text=True)
        return [int(pid) for pid in out.stdout.split()]


def install(root: Path) -> StubClaude:
    """Write a ``claude`` shim under ``root`` that execs this stub with a unique tag."""
    tag = f"stub-claude-{uuid.uuid4().hex[:12]}"
    bin_dir = root / "stub-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "claude"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).resolve()}" {tag} "$@"\n')
    shim.chmod(0o755)
    config_dir = root / "claude-config"
    config_dir.mkdir(exist_ok=True)
    return StubClaude(
        tag=tag, bin_dir=bin_dir, config_dir=config_dir, argv_log=root / "stub-argv.ndjson"
    )


def main(argv: list[str]) -> int:
    claude_argv = argv[2:]  # argv[1] is the tag
    if claude_argv[:2] == ["auth", "status"]:
        logged_in = not os.environ.get(LOGGED_OUT_ENV)
        print(
            json.dumps(
                {"loggedIn": logged_in, "authMethod": "claude.ai", "apiProvider": "firstParty"}
            )
        )
        return 0 if logged_in else 1
    log = os.environ.get(ARGV_LOG_ENV)
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(claude_argv) + "\n")
    signal.signal(signal.SIGTERM, lambda *_: os._exit(143))
    if "--input-format" not in claude_argv:
        print("stub claude: only the stream-json wire is stubbed", file=sys.stderr)
        return 97
    return Stub(claude_argv).serve()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
