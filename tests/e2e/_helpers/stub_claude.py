"""A claude stand-in that speaks the stream-json wire, for headless e2e.

It follows the contract measured on claude 2.1.281 (HATS-2025 findings, "stub
contract"): NDJSON on stdin, one line per event on stdout, and a transcript in
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

A prompt whose ``uuid`` was seen before is echoed and dropped, as claude does.

With ``--replay-user-messages`` a turn opens with the prompt's echo, and with
``--include-partial-messages`` each response is framed by ``message_start`` /
``message_delta`` / ``message_stop`` — the final usage rides the delta, as on
the live wire.

Usage: ``stub_claude.py <tag> <claude argv...>``. The tag is the stub's own
argv[1], so a test can find the process with ``pgrep -f`` and nothing else;
``auth status`` answers logged-in, as the readiness probe asks it to.
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


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _flag(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


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

    # -- wire and record ---------------------------------------------------

    def emit(self, obj: dict) -> None:
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

    def tool_result(self, call: str, content: str) -> None:
        record = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": call, "content": content}],
            },
        }
        self.record(record)
        self.emit({**record, "parent_tool_use_id": None, "session_id": self.session_id})

    def result(self, text: str, *, is_error: bool = False, stop_reason: str = "end_turn") -> None:
        self.cost += 0.001
        self.emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": is_error,
                "stop_reason": stop_reason,
                "terminal_reason": "completed",
                "result": text,
                "result_index": self.results,
                "permission_denials": [],
                "total_cost_usd": round(self.cost, 6),
                "session_id": self.session_id,
                "user_message_uuids": list(self.turn_ids),
            }
        )
        self.results += 1

    # -- a turn --------------------------------------------------------------

    def turn(self, text: str, prompt_uuid: str) -> None:
        self.init()
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
        self.run(body, text)
        if late is not None:
            records, self.deferred = self.deferred, None
            self.write_late(records, late, time.time())

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
        if body.startswith("@error"):
            detail = f"API Error: {body[len('@error') :].strip() or '529 overloaded'}"
            message = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "model": "<synthetic>",
                "content": [{"type": "text", "text": detail}],
            }
            record = {
                "type": "assistant",
                "isApiErrorMessage": True,
                "error": "server_error",
                "requestId": request,
                "message": message,
            }
            self.record(record)
            self.emit(
                {
                    "type": "assistant",
                    "is_api_error_message": True,
                    "error": "server_error",
                    "request_id": request,
                    "uuid": record["uuid"],
                    "timestamp": record["timestamp"],
                    "message": message,
                    "parent_tool_use_id": None,
                    "session_id": self.session_id,
                }
            )
            self.result(detail, is_error=True, stop_reason="stop_sequence")
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
        elif body == "@bg":
            self.respond(request, [{"type": "text", "text": "started"}], "end_turn")
            self.result("started")
            self.own_turn()
        else:
            answer = self.previous if body == "@recall" else f"ok: {body}"
            self.respond(request, [{"type": "text", "text": answer}], "end_turn")
            self.result(answer)
        self.previous = text

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
                self.inbox.put(msg)
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

    def session_env(self, project) -> dict[str, str]:
        """The env an e2e test launches ``ai-hats headless`` with: the scrubbed
        parent env, the project's pins, and this stub first on PATH."""
        from _helpers.env import clean_env  # lazy: this file also runs as the stub

        env = clean_env(os.environ)
        env.update(project.env)
        env.update(self.env(env.get("PATH", "")))
        env["AI_HATS_NO_UPDATE_CHECK"] = "1"
        return env

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
        print(
            json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"})
        )
        return 0
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
