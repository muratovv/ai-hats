"""HATS-1966 S1+S3 — ``ClaudeTranscriptReader`` against the canonical model.

Every fixture here is hand-written. A real transcript carries a session id, a
cwd and unredacted prompts, so none is ever copied into the tree; the shapes
below are the ones the corpus was *measured* to have, written out by hand.

Each test names its positive control, because the defects this reader exists to
kill all have a degenerate fix that passes a naive assertion: suppress every
repeat and the token count stops inflating, drop every text block and the
error notice stops leaking into the answer. The controls make those fail.
"""  # comment-length: allow — why hand-written fixtures, and why controls

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from ai_hats_observe.canonical import (
    Completion,
    EventReader,
    GateDecision,
    GatePoint,
    GateVerdict,
    HarnessActionRequired,
    HarnessMustAct,
    ItemEmitted,
    ItemKind,
    Notice,
    AskKind,
    PersonActionRequired,
    PersonAsked,
    PersonMustAct,
    PromptOrigin,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
    Signal,
    TextItem,
    ToolCallItem,
    ToolResultReceived,
    WorthRecording,
    collect,
)
from ai_hats_observe.parsers.claude_events import KNOWN_RECORD_TYPES, ClaudeTranscriptReader

# --- fixture builders ------------------------------------------------------

USAGE = {
    "input_tokens": 100,
    "output_tokens": 40,
    "cache_read_input_tokens": 7,
    "cache_creation_input_tokens": 3,
}


def assistant(
    request_id: str,
    content: list[dict[str, Any]],
    *,
    usage: dict[str, int] | None = None,
    stop_reason: str | None = "tool_use",
    **extra: Any,
) -> dict[str, Any]:
    """One fragment of one call, shaped like the corpus: every fragment repeats
    the call's whole ``usage`` and its final ``stop_reason``."""
    return {
        "type": "assistant",
        "requestId": request_id,
        "uuid": f"{request_id}-{len(content)}",
        "timestamp": "2026-09-12T10:00:00.000Z",
        "message": {
            "id": f"msg_{request_id}",
            "model": "claude-test-1",
            "stop_reason": stop_reason,
            "usage": dict(USAGE if usage is None else usage),
            "content": content,
        },
        **extra,
    }


def user(content: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": "u-1",
        "timestamp": "2026-09-12T10:00:01.000Z",
        "message": {"role": "user", "content": content},
    }


def api_error(error: str, text: str, **extra: Any) -> dict[str, Any]:
    record = assistant("req-err", [{"type": "text", "text": text}], stop_reason="stop_sequence")
    record["isApiErrorMessage"] = True
    record["error"] = error
    record["message"]["model"] = "<synthetic>"
    record.update(extra)
    return record


def write(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def append(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(r) + "\n" for r in records))


def events_of(tmp_path: Path, records: list[dict[str, Any]], name: str = "t.jsonl") -> list[Any]:
    return list(ClaudeTranscriptReader(write(tmp_path / name, records)).read())


def items(events: Iterator[Any] | list[Any], kind: ItemKind) -> list[Any]:
    """Things of one kind, whichever event carried them — a tool result is its
    own event now, parented by the call rather than by a response."""
    out: list[Any] = []
    for e in events:
        if isinstance(e, ItemEmitted) and e.item.kind is kind:
            out.append(e.item)
        elif isinstance(e, ToolResultReceived) and kind is ItemKind.TOOL_RESULT:
            out.append(e)
    return out


def signals(events: list[Any]) -> list[Signal]:
    return [e for e in events if isinstance(e, Signal)]


# --- the protocol ----------------------------------------------------------


def test_reader_satisfies_the_event_reader_protocol(tmp_path: Path) -> None:
    """The canonical seam, not a look-alike: consumers are written against it."""
    reader = ClaudeTranscriptReader(write(tmp_path / "t.jsonl", []))
    assert isinstance(reader, EventReader)


# --- 1. usage is counted once ----------------------------------------------


def test_usage_is_counted_once_per_response_not_once_per_fragment(tmp_path: Path) -> None:
    """One call split over 3 fragments repeating an identical usage is billed
    once — the 2.61x inflation this reader exists to kill.

    Positive control: a single-fragment call in the same file still contributes
    its full usage, and a second distinct requestId adds to the total. A reader
    that passed by suppressing repeats would lose one or both of those.
    """
    records = [
        # one call, three fragments, byte-identical usage on each
        assistant("req-a", [{"type": "thinking", "thinking": "weighing it"}]),
        assistant("req-a", [{"type": "text", "text": "first"}]),
        assistant("req-a", [{"type": "tool_use", "id": "c1", "name": "Read", "input": {}}]),
        # a single-fragment call: the control that dedup keys on identity
        assistant("req-b", [{"type": "text", "text": "second"}], stop_reason="end_turn"),
    ]
    run = collect(events_of(tmp_path, records))

    assert run.api_calls == 2, "fragments were counted as calls"
    assert run.usage.input_tokens == 2 * USAGE["input_tokens"]
    assert run.usage.output_tokens == 2 * USAGE["output_tokens"]
    assert run.usage.cache_read_input_tokens == 2 * USAGE["cache_read_input_tokens"]
    # POSITIVE CONTROL: each call contributed all of its usage, not a share
    assert [r.usage.input_tokens for r in run.responses] == [100, 100]


def test_a_single_response_is_ended_exactly_once(tmp_path: Path) -> None:
    """Three fragments, one ResponseStarted and one ResponseEnded."""
    records = [assistant("req-a", [{"type": "text", "text": f"{i}"}]) for i in range(3)]
    events = events_of(tmp_path, records)

    assert len([e for e in events if isinstance(e, ResponseStarted)]) == 1
    assert len([e for e in events if isinstance(e, ResponseEnded)]) == 1


# --- 2. text accumulates ---------------------------------------------------


def test_text_from_every_fragment_survives(tmp_path: Path) -> None:
    """The legacy parser overwrote ``current.response`` per record and lost
    3.6M characters of narration; every fragment's text must survive.

    Positive control: the count of TextItems is asserted too, so a reader that
    concatenated everything into one blob — or kept only the last — fails.
    """
    records = [
        assistant("req-a", [{"type": "text", "text": "alpha"}]),
        assistant("req-a", [{"type": "text", "text": "beta"}]),
        assistant("req-a", [{"type": "text", "text": "gamma"}], stop_reason="end_turn"),
    ]
    run = collect(events_of(tmp_path, records))

    assert len(run.responses) == 1
    texts = [i.text for i in run.responses[0].items if i.kind is ItemKind.TEXT]
    assert texts == ["alpha", "beta", "gamma"]
    assert run.responses[0].text == "alphabetagamma"


def test_thinking_is_retained_verbatim(tmp_path: Path) -> None:
    """R6: reasoning is kept, not replaced by a ``chars // 200`` pseudo-measure."""
    events = events_of(
        tmp_path,
        [assistant("req-a", [{"type": "thinking", "thinking": "the long way round"}])],
    )
    assert [i.text for i in items(events, ItemKind.THINKING)] == ["the long way round"]


# --- 3. tool linkage -------------------------------------------------------


def test_tool_results_link_back_to_their_call_for_success_and_failure(tmp_path: Path) -> None:
    """A ToolResultReceived's call_id names the ToolCallItem that asked for it, and
    ``ok`` carries ``is_error`` — 3458 failures were invisible before.

    Positive control: the successful result is asserted linked as well, so the
    test cannot pass by dropping successes and reporting only failures.
    """
    records = [
        assistant(
            "req-a",
            [
                {"type": "tool_use", "id": "call-ok", "name": "Read", "input": {"file_path": "/x"}},
                {"type": "tool_use", "id": "call-bad", "name": "Bash", "input": {"command": "no"}},
            ],
        ),
        user([{"type": "tool_result", "tool_use_id": "call-ok", "content": "contents"}]),
        user(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-bad",
                    "content": "command not found",
                    "is_error": True,
                }
            ]
        ),
    ]
    events = events_of(tmp_path, records)

    calls = {c.call_id: c for c in items(events, ItemKind.TOOL_CALL)}
    results = {r.call_id: r for r in items(events, ItemKind.TOOL_RESULT)}
    assert set(results) <= set(calls), "a result named a call that was never emitted"
    assert calls["call-bad"].name == "Bash"
    assert calls["call-ok"].input == {"file_path": "/x"}
    # POSITIVE CONTROL: both outcomes are present and linked
    assert results["call-ok"].ok is True
    assert results["call-bad"].ok is False
    assert results["call-bad"].content == "command not found"


def test_a_tool_result_names_the_call_that_asked_not_the_latest_one(tmp_path: Path) -> None:
    """Two calls in flight: each result names its own call, including a late one."""
    records = [
        assistant("req-a", [{"type": "tool_use", "id": "c-a", "name": "Read", "input": {}}]),
        user([{"type": "tool_result", "tool_use_id": "c-a", "content": "A"}]),
        assistant("req-b", [{"type": "tool_use", "id": "c-b", "name": "Read", "input": {}}]),
        user([{"type": "tool_result", "tool_use_id": "c-b", "content": "B"}]),
        # a late result for the first call must still name that first call
        user([{"type": "tool_result", "tool_use_id": "c-a", "content": "A-again"}]),
    ]
    answered = {
        e.content: e.call_id
        for e in events_of(tmp_path, records)
        if isinstance(e, ToolResultReceived)
    }
    assert answered == {"A": "c-a", "B": "c-b", "A-again": "c-a"}


# --- 4. contamination ------------------------------------------------------

LIMIT_NOTICE = "You've hit your monthly spend limit"


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        ("authentication_failed", PersonMustAct.REAUTHENTICATE),
        ("billing_error", PersonMustAct.PAY),
    ],
)
def test_person_must_act_errors_map_to_their_obligation(
    tmp_path: Path, error: str, expected_reason: PersonMustAct
) -> None:
    events = events_of(tmp_path, [api_error(error, "API Error: 403 Request not allowed")])
    raised = signals(events)
    assert len(raised) == 1
    assert isinstance(raised[0], PersonActionRequired)
    assert raised[0].reason is expected_reason


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        ("rate_limit", HarnessMustAct.WAIT),
        ("server_error", HarnessMustAct.RETRY),
        ("invalid_request", HarnessMustAct.ABORT),
        ("unknown", HarnessMustAct.ABORT),
    ],
)
def test_harness_must_act_errors_map_to_their_obligation(
    tmp_path: Path, error: str, expected_reason: HarnessMustAct
) -> None:
    events = events_of(tmp_path, [api_error(error, "upstream said no")])
    raised = signals(events)
    assert len(raised) == 1
    assert isinstance(raised[0], HarnessActionRequired)
    assert raised[0].reason is expected_reason


def test_an_api_error_notice_never_becomes_the_answer(tmp_path: Path) -> None:
    """The bug that made a limit notice the agent's answer in 15 runs: an
    api-error record contributes no TextItem, only a signal.

    Positive control: the same string MUST still be findable in signal.detail.
    An empty result everywhere would mean the parse broke, not that the
    contamination was fixed.
    """
    records = [
        assistant("req-a", [{"type": "text", "text": "the real answer"}], stop_reason="end_turn"),
        api_error(
            "rate_limit",
            LIMIT_NOTICE,
            apiErrorStatus=429,
            quotaLimits={"resetsAt": 1788529800, "rateLimitType": "five_hour"},
        ),
    ]
    events = events_of(tmp_path, records)

    assert all(LIMIT_NOTICE not in item.text for item in items(events, ItemKind.TEXT))
    # POSITIVE CONTROL: the parse did run and the text is on the signal
    raised = signals(events)
    assert len(raised) == 1
    assert raised[0].detail == LIMIT_NOTICE
    assert raised[0].raw_code == "429"
    assert raised[0].source == "claude/jsonl"
    assert raised[0].retry_after == 1788529800
    # the genuine answer is untouched
    assert [i.text for i in items(events, ItemKind.TEXT)] == ["the real answer"]


def test_an_api_error_record_is_not_counted_as_an_api_call(tmp_path: Path) -> None:
    """Claude stamps these ``model: "<synthetic>"`` with a zeroed usage — they
    are a harness notice, not an inference call, and must not inflate the count."""
    run = collect(events_of(tmp_path, [api_error("server_error", "529")]))
    assert run.api_calls == 0
    assert run.blocked_by is not None


# --- 5. record-type classification -----------------------------------------


def test_an_unmodelled_record_type_is_reported_once_with_its_raw_type(tmp_path: Path) -> None:
    """R5: drift is reported, never dropped.

    Positive control: all 23 known types in the same file produce no signal at
    all, so the assertion cannot be satisfied by a detector that fires always
    (or by one that is dead and fires never).
    """
    known: list[dict[str, Any]] = [
        {"type": t, "uuid": f"k-{t}"} for t in sorted(KNOWN_RECORD_TYPES)
    ]
    # `system` is classified by subtype, so the control feeds it a real one
    known = [r | {"subtype": "turn_duration"} if r["type"] == "system" else r for r in known]
    events = events_of(tmp_path, [*known, {"type": "not-a-real-type", "uuid": "x"}])

    drift = [
        s
        for s in signals(events)
        if isinstance(s, Notice) and s.reason is WorthRecording.UNSUPPORTED_RECORD
    ]
    assert len(drift) == 1
    assert drift[0].raw_code == "not-a-real-type"
    assert drift[0].source == "claude/jsonl"


def test_summary_is_not_carried_over_from_the_legacy_known_set(tmp_path: Path) -> None:
    """``usage._KNOWN_TYPES`` lists ``summary``; it occurs zero times."""
    assert "summary" not in KNOWN_RECORD_TYPES
    events = events_of(tmp_path, [{"type": "summary", "uuid": "s"}])
    assert [s.raw_code for s in signals(events)] == ["summary"]


# --- 6. resumability -------------------------------------------------------


def test_a_second_read_yields_only_what_was_appended(tmp_path: Path) -> None:
    """The reader holds its position, so following a growing source re-announces
    nothing — which is what makes exactly-once emission true.

    Positive control: the first read is asserted non-empty and the second is
    asserted to carry the new response, so a reader that simply yielded nothing
    on the second call would fail.
    """
    path = write(
        tmp_path / "live.jsonl",
        [
            user("start here"),
            assistant("req-a", [{"type": "text", "text": "one"}], stop_reason="end_turn"),
        ],
    )
    reader = ClaudeTranscriptReader(path)

    first = list(reader.read())
    assert [e.text for e in first if isinstance(e, PromptReceived)] == ["start here"]
    assert [i.text for i in items(first, ItemKind.TEXT)] == ["one"]
    assert reader.exhausted is True

    append(
        path,
        [assistant("req-b", [{"type": "text", "text": "two"}], stop_reason="end_turn")],
    )
    second = list(reader.read())

    assert [i.text for i in items(second, ItemKind.TEXT)] == ["two"]
    assert [e.response_id for e in second if isinstance(e, ResponseStarted)] == ["req-b"]
    # POSITIVE CONTROL: nothing from the first read came back
    assert not any(isinstance(e, PromptReceived) for e in second)
    assert collect(first + second).api_calls == 2


def test_a_live_reader_holds_the_tail_open_until_the_run_is_declared_over(
    tmp_path: Path,
) -> None:
    """A response still being written has simply not produced its ResponseEnded
    — the only representation of "in flight" the model has."""
    path = write(tmp_path / "live.jsonl", [assistant("req-a", [{"type": "thinking", "t": ""}])])
    reader = ClaudeTranscriptReader(path, live=True)

    first = list(reader.read())
    assert any(isinstance(e, ResponseStarted) for e in first)
    assert not any(isinstance(e, ResponseEnded) for e in first)
    assert reader.exhausted is False

    reader.close()
    tail = list(reader.read())
    assert [e.response_id for e in tail if isinstance(e, ResponseEnded)] == ["req-a"]
    assert reader.exhausted is True


def test_a_half_written_final_line_is_held_back_until_it_is_complete(tmp_path: Path) -> None:
    """A live tail must never be parsed from a line the surface is mid-write on."""
    path = tmp_path / "live.jsonl"
    record = json.dumps(assistant("req-a", [{"type": "text", "text": "done"}]))
    path.write_text(record[:40], encoding="utf-8")
    reader = ClaudeTranscriptReader(path, live=True)

    assert list(reader.read()) == []
    path.write_text(record + "\n", encoding="utf-8")
    assert [i.text for i in items(list(reader.read()), ItemKind.TEXT)] == ["done"]


# --- completion mapping ----------------------------------------------------


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("end_turn", Completion.COMPLETE),
        ("tool_use", Completion.COMPLETE),
        ("stop_sequence", Completion.COMPLETE),
        ("max_tokens", Completion.TRUNCATED_BUDGET),
        ("refusal", Completion.REFUSED),
    ],
)
def test_stop_reason_maps_to_a_completion(
    tmp_path: Path, stop_reason: str, expected: Completion
) -> None:
    events = events_of(
        tmp_path,
        [assistant("req-a", [{"type": "text", "text": "x"}], stop_reason=stop_reason)],
    )
    ended = [e for e in events if isinstance(e, ResponseEnded)]
    assert [e.completion for e in ended] == [expected]
    assert ended[0].stop_reason == stop_reason


def test_transport_truncation_outranks_the_reported_stop_reason(tmp_path: Path) -> None:
    """``truncatedAfterOutput`` says the connection died mid-answer; the
    ``stop_sequence`` the record also carries is not what happened."""
    events = events_of(
        tmp_path,
        [
            assistant(
                "req-a",
                [{"type": "text", "text": "half an ans"}],
                stop_reason="stop_sequence",
                truncatedAfterOutput=True,
            )
        ],
    )
    ended = [e for e in events if isinstance(e, ResponseEnded)]
    assert [e.completion for e in ended] == [Completion.TRUNCATED_TRANSPORT]


def test_input_exhausted_with_no_reported_outcome_ends_unknown(tmp_path: Path) -> None:
    """Not COMPLETE and not a reported truncation — our admission that we never
    saw the surface say why. ``stop_reason: None`` alone is mid-response, so the
    verdict only lands when the input runs out."""
    events = events_of(
        tmp_path,
        [
            assistant("req-a", [{"type": "thinking", "thinking": "..."}], stop_reason=None),
            assistant("req-a", [{"type": "text", "text": "cut"}], stop_reason=None),
        ],
    )
    ended = [e for e in events if isinstance(e, ResponseEnded)]
    assert [e.completion for e in ended] == [Completion.UNKNOWN]
    # POSITIVE CONTROL: the fragments were read, so UNKNOWN is a verdict not a crash
    assert len(items(events, ItemKind.TEXT)) == 1


# --- system subtypes -------------------------------------------------------


def test_a_model_refusal_fallback_names_the_model_that_took_over(tmp_path: Path) -> None:
    events = events_of(
        tmp_path,
        [
            {
                "type": "system",
                "subtype": "model_refusal_fallback",
                "uuid": "s1",
                "content": "Safeguards flagged this message. Switched to Opus 4.8. Send feedback",
                "originalModel": "claude-fable-5-1",
                "fallbackModel": "claude-opus-4-8",
            }
        ],
    )
    raised = signals(events)
    assert [s.reason for s in raised] == [WorthRecording.MODEL_SWITCHED]
    assert raised[0].model == "claude-opus-4-8"


def test_a_model_refusal_fallback_reads_the_model_out_of_prose_when_unnamed(
    tmp_path: Path,
) -> None:
    """Positive control for the field-first read: with ``fallbackModel`` absent
    the prose still yields a model, so the field path is not the only one."""
    events = events_of(
        tmp_path,
        [
            {
                "type": "system",
                "subtype": "model_refusal_fallback",
                "uuid": "s1",
                "content": "Safeguards flagged this. Switched to Opus 4.8. Send feedback",
            }
        ],
    )
    assert signals(events)[0].model == "Opus 4.8"


def test_a_fallback_block_is_a_model_switch_naming_the_model_that_took_over(
    tmp_path: Path,
) -> None:
    """The API rerouting one call mid-response arrives as a content block,
    ``{"type": "fallback", "from": {"model": …}, "to": {"model": …}}`` — 14 in
    the measured corpus, every one of them reported as drift until now."""
    events = events_of(
        tmp_path,
        [
            assistant(
                "req-a",
                [
                    {
                        "type": "fallback",
                        "from": {"model": "claude-fable-5-1"},
                        "to": {"model": "claude-opus-5"},
                    },
                    {"type": "text", "text": "kept"},
                ],
            )
        ],
    )
    raised = signals(events)
    assert [s.reason for s in raised] == [WorthRecording.MODEL_SWITCHED]
    assert raised[0].model == "claude-opus-5"
    assert raised[0].raw_code == "block:fallback"
    assert raised[0].detail == "claude-fable-5-1 → claude-opus-5"
    # POSITIVE CONTROL: the block is read, not skipped — the text beside it survives
    assert [i.text for i in items(events, ItemKind.TEXT)] == ["kept"]


def test_a_compact_boundary_is_recorded(tmp_path: Path) -> None:
    """Context overflow never arrives as an error — Claude compacts instead."""
    events = events_of(
        tmp_path,
        [
            {
                "type": "system",
                "subtype": "compact_boundary",
                "uuid": "s1",
                "content": "Conversation compacted",
                "level": "info",
                "compactMetadata": {"trigger": "auto", "preTokens": 968112},
            }
        ],
    )
    assert [s.reason for s in signals(events)] == [WorthRecording.CONTEXT_COMPACTED]


def test_informational_warnings_are_recorded_and_chatter_is_not(tmp_path: Path) -> None:
    """A ``warning`` reaches a reader with its text; ``notice``/``info`` do not.

    Positive control: the warning is asserted present in the same file as the
    two that are skipped, so a reader that dropped all three would fail.
    """
    events = events_of(
        tmp_path,
        [
            {
                "type": "system",
                "subtype": "informational",
                "uuid": "s1",
                "level": "warning",
                "content": "Remote Control disconnected",
            },
            {
                "type": "system",
                "subtype": "informational",
                "uuid": "s2",
                "level": "notice",
                "content": "saved",
            },
            {
                "type": "system",
                "subtype": "informational",
                "uuid": "s3",
                "level": "info",
                "content": "fyi",
            },
        ],
    )
    raised = signals(events)
    assert [s.detail for s in raised] == ["Remote Control disconnected"]
    assert raised[0].raw_code == "system/informational"


def test_known_bookkeeping_subtypes_produce_no_signal(tmp_path: Path) -> None:
    """They are classified so they stop polluting the drift signal, and read so
    they stop being news."""
    quiet = [
        "turn_duration",
        "away_summary",
        "local_command",
        "scheduled_task_fire",
        "bridge_status",
    ]
    events = events_of(
        tmp_path,
        [{"type": "system", "subtype": s, "uuid": f"s-{s}"} for s in quiet],
    )
    assert signals(events) == []


def test_an_unmodelled_system_subtype_is_reported(tmp_path: Path) -> None:
    """Positive control for the test above: the quiet set is a decision, not a
    blanket ignore of ``system``."""
    events = events_of(tmp_path, [{"type": "system", "subtype": "brand-new", "uuid": "s"}])
    assert [s.raw_code for s in signals(events)] == ["system/brand-new"]


# The record Claude writes after its Stop hooks ran, field for field as sampled
# from a real transcript this session; only the command is a stand-in.
STOP_HOOK_SUMMARY: dict[str, Any] = {
    "type": "system",
    "subtype": "stop_hook_summary",
    "uuid": "s-stop",
    "timestamp": "2026-09-14T12:19:25.936Z",
    "hookCount": 1,
    "hookInfos": [{"command": "~/.hooks/notify-stop.sh", "durationMs": 171}],
    "hookErrors": [],
    "hookAdditionalContext": [],
    "preventedContinuation": False,
    "stopReason": "",
    "hasOutput": True,
    "level": "suggestion",
    "toolUseID": "ccce6922-9fcb-439d-9e49-5a71aa6be5ca",
}


def test_a_stop_hook_summary_is_a_gate_verdict_at_the_stop(tmp_path: Path) -> None:
    """Stop hooks are a gate on the run, so the record naming them reads as a
    verdict: ``allow`` when the run went on, ``deny`` when a hook prevented
    continuation — and the one hook that ran is the one that blocked."""
    blocked = {
        **STOP_HOOK_SUMMARY,
        "uuid": "s-blocked",
        "preventedContinuation": True,
        "stopReason": "tests are red",
    }
    events = events_of(tmp_path, [STOP_HOOK_SUMMARY, blocked])

    verdicts = [e for e in events if isinstance(e, GateVerdict)]
    assert [v.decision for v in verdicts] == [GateDecision.ALLOW, GateDecision.DENY]
    assert all(v.point is GatePoint.AT_STOP and v.source == "claude/jsonl" for v in verdicts)
    assert [v.hook for v in verdicts] == ["", "notify-stop.sh"], "nothing objected; then one did"
    assert verdicts[1].reason == "tests are red"
    assert verdicts[0].ts == "2026-09-14T12:19:25.936Z"
    # POSITIVE CONTROL for the run-health axis: a verdict is content, not a signal
    assert signals(events) == []


def test_a_stop_hook_that_failed_is_a_surface_warning(tmp_path: Path) -> None:
    """A hook that errored is run health worth knowing, beside the verdict.
    ``hookErrors`` is empty in every one of 400 measured transcripts, so its
    entry shape is unobserved and kept as text rather than modelled."""
    failed = {**STOP_HOOK_SUMMARY, "hookErrors": ["notify-stop.sh: exit 1"]}
    events = events_of(tmp_path, [failed])

    warnings = [s for s in signals(events) if s.reason is WorthRecording.SURFACE_WARNING]
    assert [w.detail for w in warnings] == ["notify-stop.sh: exit 1"]
    assert warnings[0].raw_code == "system/stop_hook_summary"
    assert len([e for e in events if isinstance(e, GateVerdict)]) == 1


# --- prompts ---------------------------------------------------------------


def test_user_prompt_text_becomes_a_prompt_and_tool_results_do_not(tmp_path: Path) -> None:
    """89,811 tool_result records are not people talking."""
    records = [
        user("a bare string prompt"),
        user([{"type": "text", "text": "a block prompt"}]),
        assistant("req-a", [{"type": "tool_use", "id": "c1", "name": "Read", "input": {}}]),
        user([{"type": "tool_result", "tool_use_id": "c1", "content": "file body"}]),
    ]
    events = events_of(tmp_path, records)
    assert [e.text for e in events if isinstance(e, PromptReceived)] == [
        "a bare string prompt",
        "a block prompt",
    ]
    # POSITIVE CONTROL: the tool_result was read, it just is not a prompt
    assert [r.content for r in items(events, ItemKind.TOOL_RESULT)] == ["file body"]


def test_a_prompt_arriving_mid_response_does_not_end_the_call(tmp_path: Path) -> None:
    """Queued input lands between fragments 103 times in the measured corpus.
    Ending the call there would re-open it on the next fragment and bill twice.

    Positive control: the prompt is asserted emitted, so the test cannot pass by
    ignoring interleaved user records altogether.
    """
    records = [
        assistant("req-a", [{"type": "thinking", "thinking": "mid"}]),
        user("wait, also do this"),
        assistant("req-a", [{"type": "text", "text": "ok"}], stop_reason="end_turn"),
    ]
    events = events_of(tmp_path, records)
    run = collect(events)

    assert run.api_calls == 1
    assert run.usage.input_tokens == USAGE["input_tokens"]
    assert len([e for e in events if isinstance(e, ResponseEnded)]) == 1
    # POSITIVE CONTROL: the interleaved prompt was read
    assert [e.text for e in events if isinstance(e, PromptReceived)] == ["wait, also do this"]


# --- waiting on a person ------------------------------------------------------


QUESTION = {
    "questions": [
        {"question": "Push master as is?", "header": "Push", "options": [{"label": "yes"}]},
        {"question": "Tag it too?", "header": "Tag", "options": [{"label": "no"}]},
    ]
}


def test_a_question_to_the_person_opens_a_wait_the_answer_closes(tmp_path: Path) -> None:
    """426 ``AskUserQuestion`` calls in the measured corpus, p90 62 minutes to an
    answer: a controller has to know the run is waiting on a person, not on a
    tool. The opener names the call; the wait is open while that call has no
    result — no closing event, the same reading a response in flight has."""
    records = [
        assistant(
            "req-a",
            [{"type": "tool_use", "id": "c1", "name": "AskUserQuestion", "input": QUESTION}],
        ),
    ]
    events = events_of(tmp_path, records)

    asked = [e for e in events if isinstance(e, PersonAsked)]
    assert asked == [
        PersonAsked(
            kind=AskKind.QUESTION,
            call_id="c1",
            tool="AskUserQuestion",
            detail="Push master as is?\nTag it too?",
            source="claude/jsonl",
            ts="2026-09-12T10:00:00.000Z",
        )
    ]
    # the opener follows the call it names
    calls = [e for e in events if isinstance(e, ItemEmitted) and e.item.kind is ItemKind.TOOL_CALL]
    assert events.index(calls[0]) < events.index(asked[0])
    # still open: nothing has answered
    assert not [e for e in events if isinstance(e, ToolResultReceived)]

    append(
        tmp_path / "t.jsonl",
        [user([{"type": "tool_result", "tool_use_id": "c1", "content": "answered: yes"}])],
    )
    later = list(ClaudeTranscriptReader(tmp_path / "t.jsonl").read())
    assert [(r.call_id, r.ok) for r in later if isinstance(r, ToolResultReceived)] == [("c1", True)]
    # POSITIVE CONTROL: an ordinary tool call opens no wait
    plain = events_of(
        tmp_path,
        [assistant("req-b", [{"type": "tool_use", "id": "c2", "name": "Read", "input": {}}])],
        name="plain.jsonl",
    )
    assert not [e for e in plain if isinstance(e, PersonAsked)]


@pytest.mark.parametrize(
    "extra, origin",
    [
        ({"promptSource": "typed", "origin": {"kind": "human"}}, PromptOrigin.PERSON),
        ({"promptSource": "suggestion_accepted"}, PromptOrigin.PERSON),
        ({"promptSource": "queued"}, PromptOrigin.PERSON),
        ({"promptSource": "system", "origin": {"kind": "task-notification"}}, PromptOrigin.HARNESS),
        (
            {"promptSource": "system", "origin": {"kind": "peer"}, "isMeta": True},
            PromptOrigin.HARNESS,
        ),
        ({"isMeta": True}, PromptOrigin.HARNESS),
        ({"promptSource": "sdk"}, PromptOrigin.HARNESS),
        ({}, None),
    ],
)
def test_a_prompt_says_whether_a_person_or_the_harness_wrote_it(
    tmp_path: Path, extra: dict[str, Any], origin: PromptOrigin | None
) -> None:
    """Skill bodies, sub-agent hand-backs and task notifications arrive as user
    records too — 10 of 14 prompts in one measured session. A controller waiting
    for the person to come back needs the record's own word on who wrote it,
    and silence when the record has none."""
    record = user("some input")
    record.update(extra)
    prompts = [e for e in events_of(tmp_path, [record]) if isinstance(e, PromptReceived)]
    assert [(e.text, e.origin) for e in prompts] == [("some input", origin)]


# --- a refusal by the surface's own gate --------------------------------------


CLASSIFIER_DENY = (
    "Permission for this action was denied by the Claude Code auto mode "
    "classifier. Reason: Blocked by classifier. If you have other tasks that "
    "don't depend on this action, continue working on those."
)
PERSON_DENY = (
    "The user doesn't want to proceed with this tool use. The tool use was "
    "rejected (eg. if it was a file edit, the new_string was NOT written to "
    "the file). STOP what you are doing and wait for the user to tell you how "
    "to proceed."
)


def _denied(call_id: str, prose: str) -> dict[str, Any]:
    return user(
        [{"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": prose}]
    )


@pytest.mark.parametrize(
    "prose, hook, reason",
    [
        (CLASSIFIER_DENY, "auto-mode-classifier", "Blocked by classifier."),
        (PERSON_DENY, "person", ""),
    ],
)
def test_a_refusal_by_the_surfaces_own_gate_is_a_verdict_not_just_a_failed_call(
    tmp_path: Path, prose: str, hook: str, reason: str
) -> None:
    """A tool the surface refused and a tool that failed are the same
    ``is_error`` result; only the prose says which, and a controller cannot act
    on prose. 51 in the measured corpus — 28 by the auto-mode classifier, 23 by
    a person. The verdict names the decider; the result still says what the
    model was told."""
    records = [
        assistant("req-a", [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {}}]),
        _denied("c1", prose),
    ]
    events = events_of(tmp_path, records)

    verdicts = [e for e in events if isinstance(e, GateVerdict)]
    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert (verdict.point, verdict.decision) == (GatePoint.BEFORE_TOOL, GateDecision.DENY)
    assert (verdict.hook, verdict.call_id, verdict.tool) == (hook, "c1", "Bash")
    assert verdict.reason.startswith(reason)
    assert verdict.source == "claude/jsonl"
    # the verdict lands before the result it explains
    results = [e for e in events if isinstance(e, ToolResultReceived)]
    assert events.index(verdict) < events.index(results[0])
    # POSITIVE CONTROL: the result is still recorded, unchanged
    assert (results[0].call_id, results[0].ok) == ("c1", False)


def test_a_tool_that_merely_failed_is_not_a_refusal(tmp_path: Path) -> None:
    """Positive control for the pattern above: an ordinary failure carries the
    same ``is_error`` flag and must produce no verdict — otherwise every red
    test run would read as a gate refusing the call."""
    records = [
        assistant("req-a", [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {}}]),
        _denied("c1", "pytest: 3 failed, 1 passed"),
    ]
    events = events_of(tmp_path, records)

    assert not [e for e in events if isinstance(e, GateVerdict)]
    assert [(r.call_id, r.ok) for r in items(events, ItemKind.TOOL_RESULT)] == [("c1", False)]


# --- a person interrupting ---------------------------------------------------


def test_an_interrupt_mid_answer_cancels_the_response_and_is_not_a_prompt(tmp_path: Path) -> None:
    """``[Request interrupted by user]`` is a marker the harness writes as user
    text (29 in the measured corpus); it is not input. The response it cut is
    over, and the only word for how is ``CANCELLED`` — the model never reported
    a stop. Positive control: the real prompt after it is still a prompt."""
    records = [
        assistant("req-a", [{"type": "thinking", "thinking": "half"}], stop_reason=None),
        user([{"type": "text", "text": "[Request interrupted by user]"}]),
        user("do the other thing instead"),
    ]
    events = events_of(tmp_path, records)

    assert [e.text for e in events if isinstance(e, PromptReceived)] == [
        "do the other thing instead"
    ]
    ended = [e for e in events if isinstance(e, ResponseEnded)]
    assert [(e.response_id, e.completion) for e in ended] == [("req-a", Completion.CANCELLED)]
    notices = [n for n in signals(events) if isinstance(n, Notice)]
    assert [(n.reason, n.raw_code) for n in notices] == [
        (WorthRecording.INTERRUPTED, "[Request interrupted by user]")
    ]
    assert notices[0].ts == "2026-09-12T10:00:01.000Z"
    # the response is ended AT the marker, not held open for the next call
    assert events.index(ended[0]) < events.index(notices[0])


def test_an_interrupt_of_a_tool_keeps_the_model_s_own_stop_reason(tmp_path: Path) -> None:
    """``… for tool use]`` (22 in the corpus) follows a tool_result the harness
    refused; the model had already stopped to ask for the tool, so its
    outcome stands — the person cancelled the tool, not the answer."""
    records = [
        assistant("req-a", [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {}}]),
        user(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "is_error": True,
                    "content": "The user doesn't want to proceed with this tool use.",
                }
            ]
        ),
        user([{"type": "text", "text": "[Request interrupted by user for tool use]"}]),
    ]
    events = events_of(tmp_path, records)

    assert not [e for e in events if isinstance(e, PromptReceived)]
    ended = [e for e in events if isinstance(e, ResponseEnded)]
    assert [(e.completion, e.stop_reason) for e in ended] == [(Completion.COMPLETE, "tool_use")]
    assert [n.reason for n in signals(events) if isinstance(n, Notice)] == [
        WorthRecording.INTERRUPTED
    ]
    # POSITIVE CONTROL: the refused result was still read
    assert [r.ok for r in items(events, ItemKind.TOOL_RESULT)] == [False]


# --- fail-soft -------------------------------------------------------------


def test_a_malformed_line_is_reported_and_the_parse_continues(tmp_path: Path) -> None:
    """Never raise: the whole corpus must parse.

    Positive control: the good records on both sides of the bad lines are
    asserted present, so the test cannot pass by abandoning the file.
    """
    path = tmp_path / "mal.jsonl"
    path.write_text(
        json.dumps(assistant("req-a", [{"type": "text", "text": "before"}], stop_reason="end_turn"))
        + "\nthis is not json at all\n42\n"
        + json.dumps(
            assistant("req-b", [{"type": "text", "text": "after"}], stop_reason="end_turn")
        )
        + "\n",
        encoding="utf-8",
    )
    events = list(ClaudeTranscriptReader(path).read())

    assert [i.text for i in items(events, ItemKind.TEXT)] == ["before", "after"]
    assert sorted(s.raw_code for s in signals(events)) == ["malformed-json", "non-object-line"]


def test_a_record_holding_a_line_separator_is_one_record(tmp_path: Path) -> None:
    """Only a newline ends a record. Six records in the measured corpus carry a
    raw U+2028 inside a text block; ``str.splitlines`` cut them into 94 pieces
    and reported every piece as malformed — for zero malformed records.

    Positive control: the test above still sees a genuinely malformed line.
    """
    text = "first line second line third\x0cfourth"
    record = assistant("req-a", [{"type": "text", "text": text}], stop_reason="end_turn")
    path = tmp_path / "sep.jsonl"
    # Claude writes the separator raw, not as   — so must the fixture
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    events = list(ClaudeTranscriptReader(path).read())

    assert [i.text for i in items(events, ItemKind.TEXT)] == [text]
    assert signals(events) == []


def test_an_unknown_content_block_is_reported_not_dropped(tmp_path: Path) -> None:
    """A block type outside the measured set is drift, and drift is said."""
    events = events_of(
        tmp_path,
        [
            assistant(
                "req-a",
                [{"type": "hologram", "data": "?"}, {"type": "text", "text": "kept"}],
            )
        ],
    )
    assert [s.raw_code for s in signals(events)] == ["block:hologram"]
    assert [i.text for i in items(events, ItemKind.TEXT)] == ["kept"]


def test_a_tool_result_with_no_known_call_is_reported_not_misattributed(tmp_path: Path) -> None:
    """Attributing it to whatever ran last would invent a link the transcript
    does not carry."""
    events = events_of(tmp_path, [user([{"type": "tool_result", "tool_use_id": "ghost"}])])
    assert [s.raw_code for s in signals(events)] == ["orphan-tool-result"]
    assert items(events, ItemKind.TOOL_RESULT) == []


def test_a_missing_source_yields_nothing_rather_than_raising(tmp_path: Path) -> None:
    """A transcript a run has not written yet is the normal empty case."""
    reader = ClaudeTranscriptReader(tmp_path / "never-written.jsonl")
    assert list(reader.read()) == []


def test_a_response_with_no_identifying_field_still_parses(tmp_path: Path) -> None:
    """26 of 40 sampled error records carry no requestId; message.id and uuid
    are the fallbacks, in that order."""
    record = {
        "type": "assistant",
        "uuid": "only-a-uuid",
        "message": {"model": "m", "stop_reason": "end_turn", "content": []},
    }
    events = events_of(tmp_path, [record])
    assert [e.response_id for e in events if isinstance(e, ResponseStarted)] == ["only-a-uuid"]


# --- shapes the reader must not invent -------------------------------------


def test_items_carry_the_canonical_types(tmp_path: Path) -> None:
    """A consumer matches on these classes; a look-alike would silently miss."""
    records = [
        assistant("req-a", [{"type": "tool_use", "id": "c1", "name": "Read", "input": {"a": 1}}]),
        user([{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}]),
        assistant("req-b", [{"type": "text", "text": "t"}], stop_reason="end_turn"),
    ]
    events = events_of(tmp_path, records)
    kinds = {type(e.item) for e in events if isinstance(e, ItemEmitted)}
    kinds |= {type(e) for e in events if isinstance(e, ToolResultReceived)}
    assert kinds == {ToolCallItem, ToolResultReceived, TextItem}


def test_no_item_ever_arrives_after_its_response_has_ended(tmp_path: Path) -> None:
    """The contract the tool-result re-parenting bought.

    A response used to be held open across a tool round-trip so its result had
    somewhere to live, which put its cost after the moment the model stopped.
    The result is parented by its call now, so this holds.

    Positive control: the fixture's first response emits three items before it
    ends, so the assertion cannot be satisfied by a reader that emits nothing.
    """
    records = [
        assistant(
            "req-a",
            [
                {"type": "thinking", "thinking": "considering"},
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "ls"}},
            ],
        ),
        user([{"type": "tool_result", "tool_use_id": "c1", "content": "out"}]),
        assistant("req-b", [{"type": "text", "text": "done"}], stop_reason="end_turn"),
    ]
    events = events_of(tmp_path, records)

    ended: set[str] = set()
    emitted_per_response: dict[str, int] = {}
    for event in events:
        if isinstance(event, ResponseEnded):
            ended.add(event.response_id)
        elif isinstance(event, ItemEmitted):
            assert event.response_id not in ended, (
                f"{event.item.kind} arrived after {event.response_id} had ended"
            )
            emitted_per_response[event.response_id] = (
                emitted_per_response.get(event.response_id, 0) + 1
            )

    # POSITIVE CONTROL: items really were emitted, so the loop above had work
    assert emitted_per_response["req-a"] == 3
    assert ended == {"req-a", "req-b"}
