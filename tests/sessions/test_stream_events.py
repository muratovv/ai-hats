"""Unit tests for ``ai_hats.surfaces.claude.stream_events`` (HATS-1966).

The SDK dataclasses are real (constructed directly); no client is opened and no
``claude`` binary is spawned, so a message sequence that only happens on a bad
day — a spend limit, a rejected quota, a message class we have never seen — is
driven deterministically.
"""

from __future__ import annotations

from typing import Any

import asyncio

import pytest

from ai_hats_observe.canonical import (
    AsyncEventReader,
    Completion,
    HarnessActionRequired,
    HarnessMustAct,
    ItemEmitted,
    ToolResultReceived,
    ItemKind,
    Notice,
    PersonActionRequired,
    PersonMustAct,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
    WorthRecording,
    collect,
)
from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from ai_hats.surfaces.claude.stream_events import SOURCE, ClaudeStreamReader

# ---------------------------------------------------------------------------
# Message constructors
# ---------------------------------------------------------------------------


def _assistant(
    *blocks,
    message_id: str | None = "msg_01",
    model: str = "claude-haiku-4-5",
    usage: dict | None = None,
    stop_reason: str | None = None,
    error: str | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model=model,
        error=error,
        usage=usage,
        message_id=message_id,
        stop_reason=stop_reason,
        session_id="sid",
        uuid=None,
    )


def _usage(inp: int = 10, out: int = 5) -> dict:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": 1,
        "cache_creation_input_tokens": 2,
    }


def _result(
    *,
    subtype: str = "success",
    is_error: bool = False,
    stop_reason: str | None = "end_turn",
    errors: list[str] | None = None,
    api_error_status: int | None = None,
    terminal_reason: str | None = "completed",
    permission_denials: list | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=9,
        is_error=is_error,
        num_turns=1,
        session_id="sid",
        stop_reason=stop_reason,
        total_cost_usd=0.01,
        usage={"input_tokens": 999, "output_tokens": 999},
        result="done",
        errors=errors,
        api_error_status=api_error_status,
        permission_denials=permission_denials,
        terminal_reason=terminal_reason,
    )


def _rate_limit(status: str, *, resets_at: int | None = 1767225600) -> RateLimitEvent:
    return RateLimitEvent(
        rate_limit_info=RateLimitInfo(
            status=status,
            resets_at=resets_at,
            rate_limit_type="five_hour",
            utilization=0.97,
            raw={"status": status},
        ),
        uuid="u1",
        session_id="sid",
    )


def drain(messages) -> list:
    """Every event the reader produces for ``messages``, in order."""
    reader = ClaudeStreamReader(messages)

    async def _go() -> list:
        return [event async for event in reader.read()]

    events = asyncio.run(_go())
    assert reader.exhausted is True
    return events


def _items(events, kind: ItemKind) -> list:
    """Things of one kind, whichever event carried them — a tool result is its
    own event now, parented by the call rather than by a response."""
    out: list[Any] = []
    for e in events:
        if isinstance(e, ItemEmitted) and e.item.kind is kind:
            out.append(e.item)
        elif isinstance(e, ToolResultReceived) and kind is ItemKind.TOOL_RESULT:
            out.append(e)
    return out


def _signals(events) -> list:
    return [
        e for e in events if isinstance(e, (Notice, PersonActionRequired, HarnessActionRequired))
    ]


# ---------------------------------------------------------------------------
# 1. A normal turn
# ---------------------------------------------------------------------------


class TestNormalTurn:
    def test_started_items_ended_in_order(self):
        events = drain(
            [
                UserMessage(content="do the thing"),
                _assistant(
                    ThinkingBlock(thinking="hmm", signature="sig"),
                    TextBlock(text="on it"),
                    ToolUseBlock(id="call_1", name="Bash", input={"command": "ls"}),
                    usage=_usage(),
                    stop_reason="tool_use",
                ),
                UserMessage(
                    content=[ToolResultBlock(tool_use_id="call_1", content="a.txt", is_error=None)]
                ),
                _assistant(TextBlock(text="there is one file"), message_id="msg_02"),
                _result(),
            ]
        )

        kinds = [type(e).__name__ for e in events]
        assert kinds == [
            "PromptReceived",
            "ResponseStarted",
            "ItemEmitted",  # thinking
            "ItemEmitted",  # text
            "ItemEmitted",  # tool call
            "ToolResultReceived",  # parented by the call, not the response
            "ResponseEnded",  # closed when the next call starts
            "ResponseStarted",
            "ItemEmitted",
            "ResponseEnded",  # closed by the result message
        ]
        assert events[0] == PromptReceived(text="do the thing")

    def test_usage_and_completion_ride_one_response_ended(self):
        events = drain(
            [
                _assistant(TextBlock(text="hi"), usage=_usage(), stop_reason="end_turn"),
                _result(),
            ]
        )

        ended = [e for e in events if isinstance(e, ResponseEnded)]
        assert len(ended) == 1
        assert ended[0].response_id == "msg_01"
        assert ended[0].usage.input_tokens == 10
        assert ended[0].usage.output_tokens == 5
        assert ended[0].completion is Completion.COMPLETE
        assert ended[0].stop_reason == "end_turn"

    def test_one_call_split_across_messages_is_counted_once(self):
        """The CLI repeats a call's usage on every fragment it splits it into —
        the fragments are one response, billed once."""
        events = drain(
            [
                _assistant(TextBlock(text="part one "), usage=_usage(100, 50)),
                _assistant(
                    TextBlock(text="part two"), usage=_usage(100, 50), stop_reason="end_turn"
                ),
                _result(),
            ]
        )

        run = collect(events)
        assert run.api_calls == 1
        assert run.usage.output_tokens == 50
        assert run.responses[0].text == "part one part two"
        assert len([e for e in events if isinstance(e, ResponseStarted)]) == 1

    def test_blocks_map_to_their_items(self):
        events = drain(
            [
                _assistant(
                    ThinkingBlock(thinking="reasoning", signature="s"),
                    TextBlock(text="answer"),
                    ToolUseBlock(id="call_1", name="Read", input={"path": "/tmp/x"}),
                    ToolResultBlock(tool_use_id="call_1", content="boom", is_error=True),
                ),
            ]
        )

        assert _items(events, ItemKind.THINKING)[0].text == "reasoning"
        assert _items(events, ItemKind.TEXT)[0].text == "answer"
        call = _items(events, ItemKind.TOOL_CALL)[0]
        assert (call.call_id, call.name, call.input) == ("call_1", "Read", {"path": "/tmp/x"})
        result = _items(events, ItemKind.TOOL_RESULT)[0]
        assert (result.call_id, result.ok, result.content) == ("call_1", False, "boom")

    def test_tool_result_is_attributed_to_the_call_that_asked(self):
        events = drain(
            [
                _assistant(ToolUseBlock(id="call_1", name="Bash", input={}), message_id="msg_a"),
                _assistant(TextBlock(text="meanwhile"), message_id="msg_b"),
                UserMessage(content=[ToolResultBlock(tool_use_id="call_1", content="ok")]),
            ]
        )

        emitted = [e for e in events if isinstance(e, ToolResultReceived)]
        assert emitted[0].call_id == "call_1"
        # a later response ran in between, and folding still files the outcome
        # under the call that asked rather than under the most recent response
        run = collect(events)
        by_id = {r.response_id: r for r in run.responses}
        assert [r.content for r in by_id["msg_a"].results] == ["ok"]
        assert by_id["msg_b"].results == []

    def test_server_side_tools_are_tool_calls_too(self):
        """The API executed it instead of us, but the model still asked for it —
        an audit that dropped it would understate what the agent did."""
        events = drain(
            [
                _assistant(
                    ServerToolUseBlock(id="srv_1", name="web_search", input={"query": "x"}),
                    ServerToolResultBlock(
                        tool_use_id="srv_1", content={"type": "web_search_tool_result", "hits": 3}
                    ),
                )
            ]
        )

        call = _items(events, ItemKind.TOOL_CALL)[0]
        assert (call.call_id, call.name) == ("srv_1", "web_search")
        assert _items(events, ItemKind.TOOL_RESULT)[0].ok is True

    def test_a_failed_server_tool_says_so_in_its_payload(self):
        events = drain(
            [
                _assistant(
                    ServerToolResultBlock(
                        tool_use_id="srv_1", content={"type": "web_search_tool_result_error"}
                    )
                )
            ]
        )

        assert _items(events, ItemKind.TOOL_RESULT)[0].ok is False

    def test_reader_satisfies_the_protocol(self):
        assert isinstance(ClaudeStreamReader([]), AsyncEventReader)

    def test_exhausted_is_false_until_the_stream_ends(self):
        reader = ClaudeStreamReader([_assistant(TextBlock(text="hi"))])
        assert reader.exhausted is False

    def test_a_second_read_resumes_rather_than_replays(self):
        """Exactly-once across calls: what a read emitted is never re-emitted."""
        reader = ClaudeStreamReader([_assistant(TextBlock(text="hi")), _result()])

        async def _first() -> list:
            out = []
            async for event in reader.read():
                out.append(event)
                if isinstance(event, ItemEmitted):
                    break
            return out

        async def _rest() -> list:
            return [event async for event in reader.read()]

        first = asyncio.run(_first())
        rest = asyncio.run(_rest())
        assert [type(e).__name__ for e in first] == ["ResponseStarted", "ItemEmitted"]
        assert [type(e).__name__ for e in rest] == ["ResponseEnded"]


# ---------------------------------------------------------------------------
# 2 & 5. The error on an assistant message
# ---------------------------------------------------------------------------


NOTICE_TEXT = "You've hit your monthly spend limit. Try again later."


class TestAssistantError:
    def test_rate_limit_error_blocks_and_never_becomes_text(self):
        events = drain(
            [
                _assistant(TextBlock(text=NOTICE_TEXT), error="rate_limit"),
                _result(subtype="error_during_execution", is_error=True),
            ]
        )

        signals = _signals(events)
        assert len(signals) == 1
        assert isinstance(signals[0], HarnessActionRequired)
        assert signals[0].reason is HarnessMustAct.WAIT
        # The contamination this reader exists to stop: the notice is not an answer.
        assert _items(events, ItemKind.TEXT) == []
        assert collect(events).responses == []

    def test_positive_control_the_text_is_in_the_signal(self):
        """The same text the previous test proves absent from the items must be
        present in the signal — otherwise that test would pass on a dropped message."""
        events = drain([_assistant(TextBlock(text=NOTICE_TEXT), error="rate_limit")])

        assert _signals(events)[0].detail == NOTICE_TEXT

    @pytest.mark.parametrize(
        ("error", "expected_type", "expected_reason"),
        [
            ("authentication_failed", PersonActionRequired, PersonMustAct.REAUTHENTICATE),
            ("billing_error", PersonActionRequired, PersonMustAct.PAY),
            ("rate_limit", HarnessActionRequired, HarnessMustAct.WAIT),
            ("server_error", HarnessActionRequired, HarnessMustAct.RETRY),
            ("invalid_request", HarnessActionRequired, HarnessMustAct.ABORT),
            ("unknown", HarnessActionRequired, HarnessMustAct.ABORT),
        ],
    )
    def test_every_error_value_maps(self, error, expected_type, expected_reason):
        events = drain([_assistant(TextBlock(text="nope"), error=error)])

        signal = _signals(events)[0]
        assert isinstance(signal, expected_type)
        assert signal.reason is expected_reason
        assert signal.raw_code == error
        assert signal.source == SOURCE

    def test_no_error_value_is_left_unmapped(self):
        """The literal is closed at six members; a seventh would need a reading."""
        from claude_agent_sdk.types import AssistantMessageError
        from typing import get_args

        from ai_hats.surfaces.claude.stream_events import _ERROR_SIGNALS

        assert set(get_args(AssistantMessageError)) == set(_ERROR_SIGNALS)


# ---------------------------------------------------------------------------
# 3. The rate-limit event
# ---------------------------------------------------------------------------


class TestRateLimitEvent:
    def test_warning_is_a_notice_that_does_not_block(self):
        events = drain([_rate_limit("allowed_warning"), _result()])

        signal = _signals(events)[0]
        assert isinstance(signal, Notice)
        assert signal.reason is WorthRecording.APPROACHING_LIMIT
        assert signal.source == SOURCE
        assert "five_hour" in signal.detail

    def test_rejected_blocks_and_carries_when_capacity_returns(self):
        events = drain([_rate_limit("rejected")])

        signal = _signals(events)[0]
        assert isinstance(signal, HarnessActionRequired)
        assert signal.reason is HarnessMustAct.WAIT
        assert signal.retry_after == 1767225600

    def test_allowed_is_not_an_event(self):
        assert drain([_rate_limit("allowed")]) == []

    def test_the_event_is_no_longer_an_unknown_message(self):
        """It arrives today and used to degrade to ``[unknown_msg:RateLimitEvent]``."""
        signals = _signals(drain([_rate_limit("allowed_warning")]))

        assert [s.reason for s in signals] == [WorthRecording.APPROACHING_LIMIT]


# ---------------------------------------------------------------------------
# 4. Records we do not model
# ---------------------------------------------------------------------------


class TestUnsupportedRecords:
    def test_an_unhandled_class_is_named_once(self):
        class NewInSomeFutureSdk:
            pass

        events = drain([NewInSomeFutureSdk()])

        assert len(events) == 1
        assert isinstance(events[0], Notice)
        assert events[0].reason is WorthRecording.UNSUPPORTED_RECORD
        assert events[0].raw_code == "NewInSomeFutureSdk"
        assert events[0].source == SOURCE

    def test_stream_event_is_reported_not_dropped(self):
        events = drain([StreamEvent(uuid="u", session_id="s", event={"type": "text_delta"})])

        assert [e.raw_code for e in events] == ["StreamEvent"]

    def test_an_unmodelled_system_subtype_names_its_subtype(self):
        events = drain([SystemMessage(subtype="brand_new", data={"a": 1})])

        assert [e.raw_code for e in events] == ["system/brand_new"]

    def test_routine_system_chatter_is_silent(self):
        """Otherwise the drift signal fires on every single run."""
        events = drain(
            [
                SystemMessage(subtype="init", data={"tools": []}),
                SystemMessage(subtype="task_progress", data={}),
            ]
        )

        assert events == []


# ---------------------------------------------------------------------------
# System subtypes the SDK does not type
# ---------------------------------------------------------------------------


class TestSystemNotices:
    def test_compact_boundary(self):
        events = drain(
            [
                SystemMessage(
                    subtype="compact_boundary", data={"compact_metadata": {"trigger": "auto"}}
                )
            ]
        )

        assert events[0].reason is WorthRecording.CONTEXT_COMPACTED
        assert "auto" in events[0].detail

    def test_model_refusal_fallback_names_the_model_that_took_over(self):
        events = drain(
            [
                SystemMessage(
                    subtype="model_refusal_fallback",
                    data={"fallback_model": "claude-sonnet-4-5", "message": "switched"},
                )
            ]
        )

        assert events[0].reason is WorthRecording.MODEL_SWITCHED
        assert events[0].model == "claude-sonnet-4-5"
        assert events[0].detail == "switched"

    def test_informational_warning_is_recorded_and_info_is_not(self):
        warned = drain(
            [SystemMessage(subtype="informational", data={"level": "warning", "message": "slow"})]
        )
        quiet = drain(
            [SystemMessage(subtype="informational", data={"level": "info", "message": "hello"})]
        )

        assert warned[0].detail == "slow"
        assert warned[0].raw_code == "system/informational"
        assert quiet == []


# ---------------------------------------------------------------------------
# The result message
# ---------------------------------------------------------------------------


class TestResultMessage:
    def test_success_adds_no_signal_and_no_response_of_its_own(self):
        events = drain([_assistant(TextBlock(text="hi"), stop_reason="end_turn"), _result()])

        assert _signals(events) == []
        assert len([e for e in events if isinstance(e, ResponseStarted)]) == 1

    def test_run_total_usage_is_not_billed_a_second_time(self):
        run = collect(drain([_assistant(TextBlock(text="hi"), usage=_usage()), _result()]))

        assert run.usage.input_tokens == 10  # not the result's 999

    def test_a_cancelled_turn_reads_as_cancelled_not_as_a_dropped_connection(self):
        events = drain(
            [
                _assistant(TextBlock(text="half an ans")),
                _result(terminal_reason="aborted_streaming", stop_reason=None),
            ]
        )

        ended = [e for e in events if isinstance(e, ResponseEnded)][0]
        assert ended.completion is Completion.CANCELLED

    def test_http_status_chooses_who_must_act(self):
        events = drain([_result(is_error=True, api_error_status=429, errors=["overloaded"])])

        signal = _signals(events)[0]
        assert isinstance(signal, HarnessActionRequired)
        assert signal.reason is HarnessMustAct.WAIT
        assert signal.raw_code == "success/http_429"
        assert "overloaded" in signal.detail

    def test_auth_status_is_a_persons_problem(self):
        signal = _signals(drain([_result(is_error=True, api_error_status=401)]))[0]

        assert isinstance(signal, PersonActionRequired)
        assert signal.reason is PersonMustAct.REAUTHENTICATE

    def test_a_failure_already_announced_is_not_announced_again(self):
        events = drain(
            [
                _assistant(TextBlock(text=NOTICE_TEXT), error="billing_error"),
                _result(is_error=True, api_error_status=402, errors=["spend limit"]),
            ]
        )

        assert len(_signals(events)) == 1

    def test_permission_denials_ride_the_failure_they_explain(self):
        signal = _signals(drain([_result(is_error=True, permission_denials=[{"tool": "Bash"}])]))[0]

        assert "1 permission denial(s)" in signal.detail
