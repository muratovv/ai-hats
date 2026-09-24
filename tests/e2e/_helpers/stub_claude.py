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

Usage: ``stub_claude.py <tag> <claude argv...>``. The tag is the stub's own
argv[1], so a test can find the process with ``pgrep -f`` and nothing else;
``auth status`` answers logged-in, as the readiness probe asks it to.
"""  # comment-length: allow — the directive table is the stub's contract with its tests

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ARGV_LOG_ENV = "STUB_CLAUDE_ARGV"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _flag(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


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

    # -- wire and record ---------------------------------------------------

    def emit(self, obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def record(self, obj: dict) -> None:
        obj.setdefault("sessionId", self.session_id)
        obj.setdefault("uuid", str(uuid.uuid4()))
        obj.setdefault("timestamp", _now())
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(obj) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

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
        """One API response: in the record first, then on the wire."""
        message = {
            "id": f"msg_{request}",
            "role": "assistant",
            "model": self.model,
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": blocks,
        }
        self.record({"type": "assistant", "requestId": request, "message": message})
        self.emit({"type": "assistant", "message": {**message, "stop_reason": None}})

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
            }
        )
        self.results += 1

    # -- a turn --------------------------------------------------------------

    def turn(self, text: str) -> None:
        self.init()
        self.record(
            {
                "type": "user",
                "promptSource": "sdk",
                "message": {"role": "user", "content": text},
            }
        )
        body = text
        while body.startswith("@sleep "):
            _, seconds, *rest = body.split(" ", 2)
            time.sleep(float(seconds))
            body = rest[0] if rest else ""
        request = uuid.uuid4().hex[:12]
        if body.startswith("@die "):
            sys.stdout.flush()
            os._exit(int(body.split()[1]))
        if body.startswith("@error"):
            detail = f"API Error: {body[len('@error') :].strip() or '529 overloaded'}"
            self.record(
                {
                    "type": "assistant",
                    "isApiErrorMessage": True,
                    "message": {
                        "role": "assistant",
                        "model": "<synthetic>",
                        "content": [{"type": "text", "text": detail}],
                    },
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
            self.record(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": call, "content": "hi"}],
                    },
                }
            )
            self.respond(f"{request}b", [{"type": "text", "text": "done"}], "end_turn")
            self.result("done")
        else:
            answer = self.previous if body == "@recall" else f"ok: {body}"
            self.respond(request, [{"type": "text", "text": answer}], "end_turn")
            self.result(answer)
        self.previous = text

    def serve(self) -> int:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"stub claude: not JSON: {line[:80]!r}", file=sys.stderr)
                continue
            if msg.get("type") != "user":
                continue
            content = msg.get("message", {}).get("content", "")
            self.turn(content if isinstance(content, str) else json.dumps(content))
        self.record({"type": "last-prompt", "lastPrompt": self.previous})
        return 0


@dataclass(frozen=True)
class StubClaude:
    """One installed stub: put :meth:`env` into the session's environment."""

    tag: str
    bin_dir: Path
    config_dir: Path
    argv_log: Path

    def env(self, path: str) -> dict[str, str]:
        return {
            "PATH": os.pathsep.join([str(self.bin_dir), path]),
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            ARGV_LOG_ENV: str(self.argv_log),
        }

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
