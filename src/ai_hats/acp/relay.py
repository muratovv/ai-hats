"""The loop between an ACP client and the grok child.

One thread, two pipes. A second prompt stays here until the first one's result
has been written to the client. Stdout of this process is the client's ACP,
never a copy of the journal.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
from typing import Any

from ai_hats_observe.canonical import (
    AgentId,
    ContextUsage,
    PromptId,
    PromptOrigin,
    PromptReceived,
    SessionConfigured,
    ToolCallId,
    ToolResultReceived,
    TurnEnded,
)
from ai_hats_observe.canonical.types import now

from .translate import (
    AskClient,
    ReplyToChild,
    answer_for_child,
    child_id_from_update,
    classify_commands,
    elicitation_declared,
    forward_update,
    full_config,
    kept_options,
    message_kind,
    our_config,
    permission_for_client,
    present_question,
    project_commands,
    prompt_id,
    prompt_text,
    rejection_verdict,
    usage_update,
    used_tokens,
    window_size,
    with_log_path,
    with_mode_options,
)


class Relay:
    """Forward ACP, and substitute the few fields the child does not have."""

    def __init__(
        self,
        *,
        client_in: int,
        client_out: int,
        child: subprocess.Popen[bytes],
        event_log: Any,
        log_path: str,
        on_session: Any = None,
        skill_names: set[str] | None = None,
    ) -> None:
        self._client_in = client_in
        self._client_out = client_out
        self._child = child
        self._event_log = event_log
        self._log_path = log_path
        self._on_session = on_session
        self._client_buf = b""
        self._child_buf = b""
        self._client_open = True
        self._awaiting_child: dict[Any, str] = {}
        self._awaiting_client: dict[Any, dict[str, Any]] = {}
        self._early: dict[Any, dict[str, Any]] = {}
        self._prompt_open: Any = None
        self._prompt_queue: list[dict[str, Any]] = []
        self._prompt_ids: dict[Any, str] = {}
        self._person_texts: set[str] = set()
        self._session_id: str | None = None
        self._window: int | None = None
        self._elicitation = False
        self._known_children: set[str] = set()
        self._child_by_call: dict[str, str] = {}
        self._skill_names = set(skill_names or ())
        self._plan = "off"
        self._permission_mode = "default"
        self._model: str | None = None
        self._commands: tuple[str, ...] = ()
        self._skills: tuple[str, ...] = ()
        self._child_options: list[dict[str, Any]] = []
        self._stdout_open = child.stdout is not None

    def run(self) -> int:
        """Talk until the child exits. Return the child's code."""
        while self._child.poll() is None or self._stdout_open or self._child_buf:
            if not self._pump():
                break
        self._close_child_stdin()
        try:
            return self._child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return self._child.poll() if self._child.poll() is not None else 1

    def _pump(self) -> bool:
        readable = self._ready()
        if readable is None:
            return False
        for fd in readable:
            if self._child.stdout is not None and fd == self._child.stdout.fileno():
                if not self._read_child():
                    return bool(self._child_buf)
            elif fd == self._client_in:
                self._read_client()
        return True

    def _ready(self) -> list[int] | None:
        watch: list[int] = []
        if self._client_open:
            watch.append(self._client_in)
        if self._child.stdout is not None and self._stdout_open:
            watch.append(self._child.stdout.fileno())
        if not watch:
            return None
        try:
            readable, _, _ = select.select(watch, [], [], 0.2)
        except (OSError, ValueError):
            return None
        return list(readable)

    def _read_client(self) -> None:
        chunk = self._read_fd(self._client_in)
        if chunk is None:
            self._client_open = False
            self._close_child_stdin()
            return
        messages, self._client_buf = _drain(self._client_buf + chunk)
        for message in messages:
            self._on_client(message)

    def _read_child(self) -> bool:
        stdout = self._child.stdout
        if stdout is None:
            self._stdout_open = False
            return False
        chunk = self._read_fd(stdout.fileno())
        if chunk is None:
            self._stdout_open = False
            messages, self._child_buf = _drain(self._child_buf)
            for message in messages:
                self._on_child(message)
            self._child_buf = b""
            return False
        messages, self._child_buf = _drain(self._child_buf + chunk)
        for message in messages:
            self._on_child(message)
        return True

    def _on_client(self, message: dict[str, Any]) -> None:
        kind = message_kind(message)
        if kind == "response":
            self._on_client_response(message)
            return
        if kind != "request" and kind != "notification":
            return
        method = message.get("method")
        if method == "initialize" and kind == "request":
            self._elicitation = elicitation_declared(message.get("params"))
        if method == "session/set_config_option" and kind == "request":
            if self._answer_our_config(message):
                return
        if method == "session/prompt" and kind == "request":
            if self._prompt_open is not None:
                self._prompt_queue.append(message)
                return
            self._start_prompt(message)
            return
        self._forward_to_child(message)

    def _on_client_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        original = self._awaiting_client.pop(request_id, None)
        if original is None:
            # The client answered before the child asked. Hold it.
            self._early[request_id] = message
            return
        self._finish_client_answer(original, message)

    def _on_child(self, message: dict[str, Any]) -> None:
        kind = message_kind(message)
        if kind == "response":
            self._on_child_response(message)
            return
        if kind == "notification":
            self._on_child_notification(message)
            return
        if kind != "request":
            return
        early = self._early.pop(message.get("id"), None)
        if early is not None:
            self._finish_client_answer(message, early)
            return
        shown = self._show_to_client(message)
        if isinstance(shown, ReplyToChild):
            self._reply_to_child(message.get("id"), shown.result, original=message)
            return
        self._awaiting_client[message.get("id")] = shown.original
        self._write_client(shown.message)

    def _show_to_client(self, message: dict[str, Any]) -> AskClient | ReplyToChild:
        decision = present_question(message, elicitation=self._elicitation)
        if not isinstance(decision, AskClient):
            return decision
        if decision.message.get("method") == "session/request_permission":
            return AskClient(
                permission_for_client(
                    decision.message,
                    known_ids=self._known_children,
                    by_call=self._child_by_call,
                ),
                decision.original,
            )
        return decision

    def _on_child_notification(self, message: dict[str, Any]) -> None:
        if message.get("method") != "session/update":
            self._write_client(message)
            return
        params = message.get("params") or {}
        update = params.get("update") or {}
        if not isinstance(update, dict):
            self._write_client(message)
            return
        child = child_id_from_update(update)
        if child:
            self._known_children.add(child)
            call_id = update.get("toolCallId")
            if isinstance(call_id, str) and call_id:
                self._child_by_call[call_id] = child
            self._emit(
                [
                    ToolResultReceived(
                        call_id=ToolCallId(str(update.get("toolCallId") or child)),
                        ok=True,
                        content={"subagent_id": child},
                        agent=AgentId(child),
                        ts=now(),
                    )
                ]
            )
        if not forward_update(update, self._person_texts):
            return
        if update.get("sessionUpdate") == "available_commands_update":
            commands, skills = classify_commands(update, self._skill_names)
            update = project_commands(update, skill_names=self._skill_names)
            self._commands = tuple(commands)
            self._skills = tuple(skills)
            self._emit_configured()
        self._write_client({**message, "params": {**params, "update": update}})

    def _on_child_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = self._awaiting_child.pop(request_id, None)
        result = message.get("result")
        if not isinstance(result, dict):
            self._write_client(message)
            self._prompt_settled(request_id, method)
            return
        if method == "initialize":
            self._window = window_size(result) or self._window
        elif method in ("session/new", "session/resume"):
            # A resumed process is a new journal. The old turns are not copied in.
            self._child_options = kept_options(result.get("configOptions"))
            result = with_log_path(result, self._log_path)
            result = with_mode_options(
                result, plan=self._plan, permission_mode=self._permission_mode
            )
            session_id = result.get("sessionId")
            if isinstance(session_id, str) and session_id:
                self._session_id = session_id
                if self._on_session is not None:
                    self._on_session(session_id)
            self._window = window_size(result) or self._window
            self._emit_configured()
        elif method == "session/prompt":
            used = used_tokens(result)
            if used is not None and self._window is not None and self._session_id:
                self._write_client(usage_update(self._session_id, used, self._window))
                self._emit([ContextUsage(used_tokens=used, max_tokens=self._window, ts=now())])
            answered = self._prompt_ids.get(request_id)
            self._emit(
                [
                    TurnEnded(
                        ok=result.get("stopReason") == "end_turn",
                        raw_code=str(result.get("stopReason") or ""),
                        prompt_ids=(PromptId(answered),) if answered else (),
                        ts=now(),
                    )
                ]
            )
        self._write_client({**message, "result": result})
        self._prompt_settled(request_id, method)

    def _start_prompt(self, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        text = prompt_text(params)
        pid = prompt_id(params)
        self._prompt_open = message.get("id")
        self._prompt_ids[message.get("id")] = pid
        if text:
            self._person_texts.add(text)
        self._emit(
            [
                PromptReceived(
                    text=text,
                    origin=PromptOrigin.PERSON,
                    prompt_id=PromptId(pid),
                    ts=now(),
                )
            ]
        )
        self._forward_to_child(message)

    def _prompt_settled(self, request_id: Any, method: str | None) -> None:
        if method != "session/prompt" or request_id != self._prompt_open:
            return
        self._prompt_open = None
        if self._prompt_queue:
            nxt = self._prompt_queue.pop(0)
            self._start_prompt(nxt)

    def _finish_client_answer(self, original: dict[str, Any], response: dict[str, Any]) -> None:
        if "error" in response and "result" not in response:
            self._write_child(response)
            return
        result = response.get("result")
        if not isinstance(result, dict):
            result = {}
        # A permission result has no text field. It goes to the child unchanged.
        body = answer_for_child(original, result)
        verdict = rejection_verdict(original, result)
        if verdict is not None:
            self._emit([verdict])
        self._reply_to_child(original.get("id"), body, original=original)

    def _reply_to_child(
        self, request_id: Any, result: dict[str, Any], *, original: dict[str, Any]
    ) -> None:
        del original
        self._write_child({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _answer_our_config(self, message: dict[str, Any]) -> bool:
        params = message.get("params") or {}
        config_id = params.get("configId")
        if not our_config(config_id):
            return False
        value = params.get("value")
        if config_id == "plan" and isinstance(value, str):
            self._plan = value
        elif config_id == "permission_mode" and isinstance(value, str):
            self._permission_mode = value
        self._emit_configured()
        self._write_client(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": full_config(
                    self._child_options, plan=self._plan, permission_mode=self._permission_mode
                ),
            }
        )
        return True

    def _emit_configured(self) -> None:
        from ai_hats_observe.canonical.types import ModelName

        self._emit(
            [
                SessionConfigured(
                    model=ModelName(self._model) if self._model else None,
                    planning=self._plan == "on",
                    permission_mode=self._permission_mode,
                    commands=self._commands,
                    skills=self._skills,
                    ts=now(),
                )
            ]
        )

    def _forward_to_child(self, message: dict[str, Any]) -> None:
        if message_kind(message) == "request":
            self._awaiting_child[message.get("id")] = str(message.get("method") or "")
        self._write_child(message)

    def _emit(self, events: list[Any]) -> None:
        if self._event_log is not None:
            self._event_log.emit(events)

    def _write_client(self, message: dict[str, Any]) -> None:
        self._write_fd(self._client_out, message)

    def _write_child(self, message: dict[str, Any]) -> None:
        stdin = self._child.stdin
        if stdin is None:
            return
        try:
            stdin.write(_line(message))
            stdin.flush()
        except (BrokenPipeError, OSError):
            return

    def _write_fd(self, fd: int, message: dict[str, Any]) -> None:
        data = _line(message)
        view = memoryview(data)
        while view:
            try:
                wrote = os.write(fd, view)
            except (BrokenPipeError, OSError):
                self._client_open = False
                return
            view = view[wrote:]

    def _close_child_stdin(self) -> None:
        stdin = self._child.stdin
        if stdin is None or stdin.closed:
            return
        try:
            stdin.close()
        except OSError:
            return

    @staticmethod
    def _read_fd(fd: int) -> bytes | None:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return None
        if not chunk:
            return None
        return chunk


def _line(message: dict[str, Any]) -> bytes:
    return json.dumps(message, ensure_ascii=False).encode() + b"\n"


def _drain(buf: bytes) -> tuple[list[dict[str, Any]], bytes]:
    """Complete JSON lines in `buf`, and the tail that is not a line yet."""
    messages: list[dict[str, Any]] = []
    while True:
        newline = buf.find(b"\n")
        if newline < 0:
            return messages, buf
        raw, buf = buf[:newline], buf[newline + 1 :]
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            messages.append(message)
