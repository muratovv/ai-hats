"""Sub-agent execution via the Claude Agent SDK — Phase 2 of HATS-474.

Replaces the legacy ``subprocess.run(["claude", "-p", ...])`` engine
inside :class:`SubAgentRunner` with :class:`ClaudeSDKClient`. The SDK
spawns the same ``claude`` binary internally over stdin/stdout JSONL, so
coverage and billing are identical to the legacy path — but we gain:

* structured turn-completion (:class:`ResultMessage` with ``subtype``)
* native cost / usage / num_turns telemetry
* in-process bidirectional control protocol (sets up Phase 3 multi-turn)
* no fragile stdout-parsing for "is the agent done"

The module is sync-on-the-outside via :func:`run_claude_sdk_blocking`
(``asyncio.run`` plus ``asyncio.wait_for`` for wall-clock cap) so
existing call-sites in :class:`SubAgentRunner._run_attempt` plug in
without an async cascade. The internal ``_run_sdk`` coroutine is what
:meth:`SubAgentRunner.session` (Phase 3) will reuse for multi-turn.

Formatters (:func:`format_transcript`, :func:`format_reasoning`) are
exposed for tests and the multi-turn API. ``format_transcript`` produces
the same shape as the legacy ``proc.stdout`` capture (so the existing
``transcript.txt`` consumers — audit-writer, judge — keep working).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SdkRunResult:
    """Outcome of a single SDK invocation.

    Mirrors what the legacy subprocess path produced (exit_code + stdout
    + stderr + optional timed_out / error), plus structured fields the
    SDK gives us natively (cost, turns, stop_reason, claude session id).
    """

    exit_code: int
    stdout: str
    stderr: str
    claude_session_id: str | None
    total_cost_usd: float | None
    num_turns: int | None
    stop_reason: str | None
    timed_out: bool
    error: str | None


# Exit codes mirror the legacy subprocess path so downstream consumers
# (auto-retro, judge guard) don't need to learn a new convention.
SDK_EXIT_SUCCESS = 0
SDK_EXIT_ERROR = 1
SDK_EXIT_TIMEOUT = 124  # GNU coreutils ``timeout`` convention, also SUBAGENT_EXIT_TIMEOUT


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_transcript(messages: list) -> str:
    """Format the user-visible transcript from a list of SDK messages.

    Mirrors the legacy ``proc.stdout`` shape — assistant TextBlock content
    in order, plus a one-line summary for each ``ToolUseBlock`` so the
    transcript records which tools the agent invoked even when their text
    output is small. Tool *results* go to ``reasoning.log`` (see
    :func:`format_reasoning`) to keep the transcript focused on what the
    agent said.

    Falls back to a textual marker for unknown block types — never raises,
    even when the SDK ships new shapes.
    """
    # Lazy import: keep framework import light when SDK isn't loaded.
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
    )

    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    parts.append(
                        f"\n[tool: {block.name}({_short_input(block.input)})]\n"
                    )
                elif isinstance(block, ThinkingBlock):
                    # Thinking is reasoning, not transcript — skip here.
                    continue
                # Tool results, server-side tool blocks: belong in reasoning.
        elif isinstance(msg, ResultMessage) and msg.result:
            # Some sub-agent shapes put the final answer in ResultMessage.result.
            # Append only if not already covered by the assistant stream.
            if not parts or parts[-1].rstrip() != msg.result.rstrip():
                parts.append(msg.result)

    text = "".join(parts).strip()
    return f"{text}\n" if text else ""


def format_reasoning(messages: list) -> str:
    """Format the diagnostic / reasoning log from SDK messages.

    Contains the data that wouldn't be useful in the user-facing
    transcript but is critical for post-hoc audit: system-level events,
    thinking-block content, tool-use args, tool results, partial errors.
    Each entry is a single tagged line for grep-friendliness.

    Like :func:`format_transcript`, this never raises — unknown shapes
    fall through to a generic ``[unknown]`` line so the log records the
    drift instead of crashing the finalize step.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            lines.append(
                f"[system:{msg.subtype}] {_json_safe(msg.data)}"
            )
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ThinkingBlock):
                    # Thinking can be multiline — keep it indented for readability.
                    body = block.thinking.replace("\n", "\n  ")
                    lines.append(f"[thinking]\n  {body}")
                elif isinstance(block, ToolUseBlock):
                    lines.append(
                        f"[tool_use:{block.name}] "
                        f"id={block.id} input={_json_safe(block.input)}"
                    )
                elif isinstance(block, ToolResultBlock):
                    lines.append(
                        f"[tool_result] tool_use_id={block.tool_use_id} "
                        f"is_error={block.is_error} content={block.content!r}"
                    )
                elif isinstance(block, TextBlock):
                    continue  # text is transcript, not reasoning
                else:
                    lines.append(f"[block:{type(block).__name__}] {block!r}")
        elif isinstance(msg, UserMessage):
            # User messages are usually our own input — we already know it.
            # But tool_result content surfaced as a user message *does* matter
            # for reasoning.
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        lines.append(
                            f"[tool_result] tool_use_id={block.tool_use_id} "
                            f"is_error={block.is_error} content={block.content!r}"
                        )
        elif isinstance(msg, ResultMessage):
            # Terminal event — record subtype and any errors for the log.
            if msg.errors:
                lines.append(f"[result:errors] {msg.errors}")
            if msg.permission_denials:
                lines.append(f"[result:permission_denials] {msg.permission_denials}")
        else:
            lines.append(f"[unknown_msg:{type(msg).__name__}] {msg!r}")

    return ("\n".join(lines) + "\n") if lines else ""


def _short_input(payload: dict) -> str:
    """One-line summary of a tool-use ``input`` dict — capped for transcript width."""
    try:
        text = json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return text if len(text) <= 120 else f"{text[:117]}..."


def _json_safe(payload) -> str:
    """JSON-serialize with ``default=str`` so dicts with paths / dates don't crash."""
    try:
        return json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(payload)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def drain_one_turn(
    client,
    message: str,
) -> tuple[SdkRunResult, list]:
    """Send one user message to an open SDK client; drain until terminal.

    Used by both the one-shot path (``_run_sdk``) and the multi-turn API
    (:class:`SubAgentSession` — HATS-474 Phase 3). The client MUST
    already be inside its ``async with`` context — this helper neither
    enters nor exits it, so it can be called repeatedly against the
    same long-lived client without re-spawning ``claude``.

    Returns ``(SdkRunResult, messages)``: the formatted result envelope
    plus the raw SDK message list so callers that want structured
    introspection (tool-call inspection, custom assertions) keep access
    to it without re-parsing the formatted transcript.

    Never re-raises — converts any per-turn exception (query failure,
    stream error) to an error :class:`SdkRunResult`. The caller decides
    whether to break the loop or call ``send`` again.
    """
    from claude_agent_sdk import ResultMessage

    messages: list = []
    result_msg: "ResultMessage | None" = None

    try:
        await client.query(message)
        async for msg in client.receive_response():
            messages.append(msg)
            if isinstance(msg, ResultMessage):
                result_msg = msg
                break
    except Exception as exc:  # noqa: BLE001 — wide net, convert to error envelope
        return (
            SdkRunResult(
                exit_code=SDK_EXIT_ERROR,
                stdout=format_transcript(messages),
                stderr=format_reasoning(messages),
                claude_session_id=None,
                total_cost_usd=None,
                num_turns=None,
                stop_reason=None,
                timed_out=False,
                error=f"{type(exc).__name__}: {exc}",
            ),
            messages,
        )

    transcript = format_transcript(messages)
    reasoning = format_reasoning(messages)

    if result_msg is None:
        # Stream ended without a terminal ResultMessage — surface as error.
        return (
            SdkRunResult(
                exit_code=SDK_EXIT_ERROR,
                stdout=transcript,
                stderr=reasoning,
                claude_session_id=None,
                total_cost_usd=None,
                num_turns=None,
                stop_reason=None,
                timed_out=False,
                error="SDK stream ended without ResultMessage",
            ),
            messages,
        )

    exit_code = SDK_EXIT_ERROR if result_msg.is_error else SDK_EXIT_SUCCESS
    return (
        SdkRunResult(
            exit_code=exit_code,
            stdout=transcript,
            stderr=reasoning,
            claude_session_id=result_msg.session_id,
            total_cost_usd=result_msg.total_cost_usd,
            num_turns=result_msg.num_turns,
            stop_reason=result_msg.stop_reason,
            timed_out=False,
            error=None,
        ),
        messages,
    )


async def _run_sdk(
    options: "ClaudeAgentOptions",
    initial_message: str,
) -> SdkRunResult:
    """Async core for the one-shot path: spawn ``ClaudeSDKClient``, send
    the initial message, drain until ``ResultMessage``, format, return.

    The wall-clock cap is applied by :func:`run_claude_sdk_blocking` via
    :func:`asyncio.wait_for` around this coroutine — kept out of the
    function body so the timeout semantic stays in one place. Never
    re-raises — converts any context-entry / per-turn exception into a
    :class:`SdkRunResult` with ``error`` populated so the caller can
    finalize uniformly.
    """
    from claude_agent_sdk import ClaudeSDKClient

    try:
        async with ClaudeSDKClient(options=options) as client:
            result, _msgs = await drain_one_turn(client, initial_message)
            return result
    except Exception as exc:  # context-entry / shutdown errors (auth, etc.)
        return SdkRunResult(
            exit_code=SDK_EXIT_ERROR,
            stdout="",
            stderr="",
            claude_session_id=None,
            total_cost_usd=None,
            num_turns=None,
            stop_reason=None,
            timed_out=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def run_claude_sdk_blocking(
    options: "ClaudeAgentOptions",
    initial_message: str,
    *,
    timeout_s: int,
) -> SdkRunResult:
    """Sync wrapper: run a single SDK attempt under a wall-clock cap.

    Returns an :class:`SdkRunResult` for every terminal path (success,
    SDK error, timeout) so the caller's finalize logic is exception-free.
    """
    async def _gated() -> SdkRunResult:
        return await asyncio.wait_for(
            _run_sdk(options, initial_message),
            timeout=timeout_s,
        )

    try:
        return asyncio.run(_gated())
    except asyncio.TimeoutError:
        return SdkRunResult(
            exit_code=SDK_EXIT_TIMEOUT,
            stdout="",
            stderr="",
            claude_session_id=None,
            total_cost_usd=None,
            num_turns=None,
            stop_reason=None,
            timed_out=True,
            error=f"SDK call exceeded timeout of {timeout_s}s",
        )
