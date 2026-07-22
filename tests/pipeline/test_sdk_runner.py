"""Unit tests for ``ai_hats.surfaces.claude.sdk_runner`` (HATS-474 Phase 2).

Covers:

* Formatters (``format_transcript``, ``format_reasoning``) — assistant
  text, tool-use, tool-result, thinking, system events, unknown shapes.
* Sync wrapper (``run_claude_sdk_blocking``) — happy path, SDK exception
  conversion, premature stream end, wall-clock timeout.

The SDK is real (imported from ``claude_agent_sdk``); the client itself
is patched per-test so we drive a deterministic message sequence into
the formatters and the wrapper without spawning the bundled ``claude``
binary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from ai_hats.surfaces.claude.sdk_runner import (
    SDK_EXIT_ERROR,
    SDK_EXIT_SUCCESS,
    SDK_EXIT_TIMEOUT,
    SdkRunResult,
    format_reasoning,
    format_transcript,
    run_claude_sdk_blocking,
)


# ---------------------------------------------------------------------------
# Message constructors — small helpers to keep tests readable
# ---------------------------------------------------------------------------


def _assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude-haiku-4-5",
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id=None,
        stop_reason=None,
        session_id=None,
        uuid=None,
    )


def _result(
    *,
    subtype: str = "success",
    is_error: bool = False,
    session_id: str = "sdk-sid-xyz",
    cost: float | None = 0.0042,
    turns: int = 3,
    stop_reason: str | None = "end_turn",
    result_text: str | None = None,
    errors: list[str] | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1000,
        duration_api_ms=900,
        is_error=is_error,
        num_turns=turns,
        session_id=session_id,
        stop_reason=stop_reason,
        total_cost_usd=cost,
        usage=None,
        result=result_text,
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        deferred_tool_use=None,
        errors=errors,
        api_error_status=None,
        uuid=None,
    )


# ---------------------------------------------------------------------------
# format_transcript
# ---------------------------------------------------------------------------


class TestFormatTranscript:
    def test_assistant_text_concatenated(self):
        msgs = [
            _assistant(TextBlock(text="Hello, ")),
            _assistant(TextBlock(text="world.")),
            _result(),
        ]
        assert format_transcript(msgs) == "Hello, world.\n"

    def test_tool_use_inlined_as_marker(self):
        msgs = [
            _assistant(
                TextBlock(text="Looking up file..."),
                ToolUseBlock(id="t1", name="Read", input={"path": "auth.py"}),
                TextBlock(text="Found it."),
            ),
            _result(),
        ]
        out = format_transcript(msgs)
        assert "Looking up file..." in out
        assert "[tool: Read(" in out
        assert "auth.py" in out
        assert "Found it." in out

    def test_thinking_excluded(self):
        msgs = [
            _assistant(
                ThinkingBlock(thinking="Let me consider...", signature="sig"),
                TextBlock(text="Done."),
            ),
            _result(),
        ]
        out = format_transcript(msgs)
        assert "consider" not in out
        assert out == "Done.\n"

    def test_result_text_appended_when_not_duplicate(self):
        msgs = [
            _assistant(TextBlock(text="In progress.")),
            _result(result_text="Final summary."),
        ]
        out = format_transcript(msgs)
        assert "In progress." in out
        assert "Final summary." in out

    def test_result_text_not_duplicated_when_already_present(self):
        """Sub-agent forms sometimes echo the final text in ResultMessage.result.
        The transcript should not show it twice.
        """
        msgs = [
            _assistant(TextBlock(text="Final summary.")),
            _result(result_text="Final summary."),
        ]
        # Either single \n at end is acceptable — but no doubled body.
        out = format_transcript(msgs)
        assert out.count("Final summary.") == 1

    def test_empty_messages_returns_empty(self):
        assert format_transcript([]) == ""

    def test_only_thinking_no_text_returns_empty(self):
        msgs = [
            _assistant(ThinkingBlock(thinking="silent", signature="s")),
            _result(),
        ]
        assert format_transcript(msgs) == ""

    def test_long_tool_input_truncated(self):
        big = {"path": "x" * 500}
        msgs = [_assistant(ToolUseBlock(id="t", name="Read", input=big)), _result()]
        out = format_transcript(msgs)
        # Should not contain the whole 500-char value verbatim
        assert "..." in out
        # Marker line should still appear once
        assert out.count("[tool: Read(") == 1


# ---------------------------------------------------------------------------
# format_reasoning
# ---------------------------------------------------------------------------


class TestFormatReasoning:
    def test_thinking_emitted(self):
        msgs = [
            _assistant(ThinkingBlock(thinking="step 1\nstep 2", signature="s")),
            _result(),
        ]
        out = format_reasoning(msgs)
        assert "[thinking]" in out
        assert "step 1" in out
        assert "step 2" in out

    def test_tool_use_and_result_lines(self):
        msgs = [
            _assistant(ToolUseBlock(id="t1", name="Read", input={"path": "x.py"})),
            UserMessage(
                content=[
                    ToolResultBlock(tool_use_id="t1", content="file body", is_error=False),
                ],
                uuid=None,
                parent_tool_use_id=None,
                tool_use_result=None,
            ),
            _result(),
        ]
        out = format_reasoning(msgs)
        assert "[tool_use:Read]" in out
        assert "id=t1" in out
        assert "[tool_result]" in out
        assert "tool_use_id=t1" in out
        assert "is_error=False" in out

    def test_system_event_emitted_with_payload(self):
        msgs = [
            SystemMessage(subtype="init", data={"model": "claude-haiku-4-5"}),
            _result(),
        ]
        out = format_reasoning(msgs)
        assert "[system:init]" in out
        assert "claude-haiku-4-5" in out

    def test_text_blocks_excluded_from_reasoning(self):
        msgs = [_assistant(TextBlock(text="answer body")), _result()]
        out = format_reasoning(msgs)
        assert "answer body" not in out

    def test_empty_returns_empty(self):
        assert format_reasoning([]) == ""

    def test_result_errors_logged(self):
        msgs = [_result(errors=["rate_limit", "retry_exhausted"])]
        out = format_reasoning(msgs)
        assert "[result:errors]" in out
        assert "rate_limit" in out


# ---------------------------------------------------------------------------
# run_claude_sdk_blocking — exercise via a stub ClaudeSDKClient
# ---------------------------------------------------------------------------


class _StubClient:
    """Minimal stand-in for ClaudeSDKClient.

    The runner uses it as an async context manager, calls ``query(...)``
    to send the initial message, then iterates ``receive_response()``.
    Tests parametrize the message sequence and any failure mode.
    """

    def __init__(self, *, messages: Iterable, raise_on_query: Exception | None = None,
                 raise_during_stream: Exception | None = None):
        self._messages = list(messages)
        self._raise_on_query = raise_on_query
        self._raise_during_stream = raise_during_stream
        self.received_query: str | None = None
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False

    async def query(self, msg: str):
        self.received_query = msg
        if self._raise_on_query is not None:
            raise self._raise_on_query

    async def receive_response(self):
        for m in self._messages:
            if self._raise_during_stream is not None:
                raise self._raise_during_stream
            yield m


@dataclass
class _CapturedInit:
    options: ClaudeAgentOptions
    last_client: _StubClient


@pytest.fixture
def patch_client(monkeypatch):
    """Patch ClaudeSDKClient to a stub factory that records the options.

    Returns a closure: ``patch_client(messages=..., raise_on_query=...,
    raise_during_stream=...)`` — call inside the test to install the
    stub, then run ``run_claude_sdk_blocking``.
    """
    captured = _CapturedInit(options=None, last_client=None)  # type: ignore[arg-type]

    def install(*, messages=(), raise_on_query=None, raise_during_stream=None):
        def _factory(options: ClaudeAgentOptions):
            captured.options = options
            client = _StubClient(
                messages=messages,
                raise_on_query=raise_on_query,
                raise_during_stream=raise_during_stream,
            )
            captured.last_client = client
            return client

        # sdk_runner imports ClaudeSDKClient inside _run_sdk lazily, so we
        # patch the name in the source module (claude_agent_sdk).
        monkeypatch.setattr(
            "claude_agent_sdk.ClaudeSDKClient",
            _factory,
        )
        return captured

    return install


def _minimal_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": ""},
    )


class TestRunClaudeSdkBlocking:
    def test_happy_path_success(self, patch_client):
        msgs = [
            _assistant(TextBlock(text="hi")),
            _result(subtype="success", session_id="claude-uuid-1", cost=0.0021, turns=1),
        ]
        cap = patch_client(messages=msgs)
        out = run_claude_sdk_blocking(
            _minimal_options(), "first message", timeout_s=10,
        )
        assert isinstance(out, SdkRunResult)
        assert out.exit_code == SDK_EXIT_SUCCESS
        assert out.stdout == "hi\n"
        assert out.claude_session_id == "claude-uuid-1"
        assert out.total_cost_usd == 0.0021
        assert out.num_turns == 1
        assert out.stop_reason == "end_turn"
        assert out.timed_out is False
        assert out.error is None
        # Initial message reached the client
        assert cap.last_client.received_query == "first message"
        # Context manager closed properly
        assert cap.last_client.entered and cap.last_client.exited

    def test_result_is_error_maps_to_exit_1(self, patch_client):
        msgs = [
            _assistant(TextBlock(text="partial")),
            _result(subtype="error_max_turns", is_error=True),
        ]
        patch_client(messages=msgs)
        out = run_claude_sdk_blocking(_minimal_options(), "msg", timeout_s=10)
        assert out.exit_code == SDK_EXIT_ERROR
        assert out.timed_out is False
        assert out.stop_reason == "end_turn"  # may or may not be set; here it is

    def test_query_exception_finalizes_with_error(self, patch_client):
        patch_client(
            messages=[],
            raise_on_query=RuntimeError("auth failed"),
        )
        out = run_claude_sdk_blocking(_minimal_options(), "msg", timeout_s=10)
        assert out.exit_code == SDK_EXIT_ERROR
        assert out.error is not None
        assert "RuntimeError" in out.error
        assert "auth failed" in out.error
        assert out.claude_session_id is None
        assert out.timed_out is False

    def test_stream_exception_finalizes_with_error(self, patch_client):
        patch_client(
            messages=[_assistant(TextBlock(text="x"))],
            raise_during_stream=ValueError("stream blew up"),
        )
        out = run_claude_sdk_blocking(_minimal_options(), "msg", timeout_s=10)
        assert out.exit_code == SDK_EXIT_ERROR
        assert "ValueError" in out.error
        assert "stream blew up" in out.error

    def test_stream_ends_without_result_message(self, patch_client):
        """If the SDK closes without emitting ResultMessage, surface as error."""
        patch_client(messages=[_assistant(TextBlock(text="hi"))])  # no ResultMessage
        out = run_claude_sdk_blocking(_minimal_options(), "msg", timeout_s=10)
        assert out.exit_code == SDK_EXIT_ERROR
        assert "without ResultMessage" in (out.error or "")
        # The assistant text we did receive is preserved
        assert "hi" in out.stdout

    def test_wall_clock_timeout(self, monkeypatch):
        """asyncio.wait_for times out the run when the SDK hangs."""
        import asyncio

        class _HangingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def query(self, msg):
                pass

            async def receive_response(self):
                await asyncio.sleep(10)
                yield  # pragma: no cover

        monkeypatch.setattr(
            "claude_agent_sdk.ClaudeSDKClient",
            lambda options: _HangingClient(),
        )
        out = run_claude_sdk_blocking(_minimal_options(), "msg", timeout_s=1)
        assert out.exit_code == SDK_EXIT_TIMEOUT
        assert out.timed_out is True
        assert out.error is not None
        assert "timeout" in out.error.lower()

    def test_options_threaded_through_to_factory(self, patch_client):
        msgs = [_result()]
        cap = patch_client(messages=msgs)
        opts = ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code", "append": "x"},
            model="claude-haiku-4-5",
            max_budget_usd=0.50,
        )
        run_claude_sdk_blocking(opts, "msg", timeout_s=10)
        assert cap.options is opts
