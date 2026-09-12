"""DRAFT (HATS-1966) — how `canonical` gets used, per consumer.

Illustrative only; deleted once the call sites it sketches are real. Each block
is one consumer. Note that no consumer holds a session unless it actually needs
a total — reading is iteration.
"""

# ruff: noqa — call sites, not code: the names they stand in for do not exist yet.

from __future__ import annotations

from ai_hats_observe.canonical import (
    ANSWER_ONLY,
    WITH_REASONING,
    Blocking,
    Completion,
    HarnessActionRequired,
    HarnessMustAct,
    ItemEmitted,
    ItemKind,
    Notice,
    PersonActionRequired,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
    Signal,
    WorthRecording,
    collect,
    select,
)
from ai_hats_observe.parsers.claude import ClaudeTranscriptReader

# ClaudeStreamReader lives in ai_hats.surfaces.claude — observe defines the
# EventReader protocol, each surface implements it, and the dependency only ever
# points that way (test_observe_boundary enforces it). Named, not imported.

# ===========================================================================
# 1. The parser. Legacy fields are folded out of the stream, so `audit.md`
#    keeps working while the stream becomes the source of truth.
# ===========================================================================


class ClaudeParser:
    def parse(self, jsonl_path, trace_path):
        run = collect(ClaudeTranscriptReader(jsonl_path).read())
        return ParsedTranscript(
            turns=[_legacy_turn(r) for r in run.responses],
            agg_usage=_as_dict(run.usage),  # the 2.61x fix, in one line
            flags=[],
            signals=run.signals,
        )


# ===========================================================================
# 2. Why the token number changes.
# ===========================================================================

# Cost arrives once, on ResponseEnded, because that is the one moment it is
# known for the whole call. There is no per-fragment usage to accidentally sum:
spend = sum(e.usage.output_tokens for e in events if isinstance(e, ResponseEnded))
calls = run.api_calls  # inference calls, not transcript records


# ===========================================================================
# 3. AuditWriter renders straight off the stream — no intermediate tree.
# ===========================================================================


def render_audit(events) -> str:
    lines = []
    for event in select(events, WITH_REASONING):
        match event:
            case PromptReceived():
                lines.append(f"👤 {event.text}")
            case ItemEmitted() if event.item.kind is ItemKind.THINKING:
                lines.append(f"💭 {event.item.text}")
            case ItemEmitted() if event.item.kind is ItemKind.TOOL_CALL:
                lines.append(f"🔧 {event.item.name}({event.item.input})")
            case ItemEmitted() if event.item.kind is ItemKind.TOOL_RESULT:
                lines.append(f"{'✅' if event.item.ok else '❌'} {event.item.content}")
            case ItemEmitted() if event.item.kind is ItemKind.TEXT:
                lines.append(f"👾 {event.item.text}")
            case ResponseEnded() if event.completion is not Completion.COMPLETE:
                lines.append(f"⚠️  {event.completion}")
            case PersonActionRequired() | HarnessActionRequired():
                lines.append(f"🛑 {event.reason}: {event.detail}")
            case Notice():
                lines.append(f"ℹ️  {event.reason}: {event.detail}")
    return "\n".join(lines)


# ===========================================================================
# 4. Projections: the same stream, narrowed per consumer.
# ===========================================================================

judge_sees = select(events, ANSWER_ONLY)  # reasoning withheld — a judge
# scores the answer, not the route
comparison_sees = select(events, WITH_REASONING)  # an A/B run compares the route

# Both still see every signal, so neither can mistake a killed run for a clean one.


# ===========================================================================
# 5. sdk_runner.
# ===========================================================================


async def drain_one_turn(client, message):
    transcript = []
    blocked = None

    async for event in ClaudeStreamReader(client.receive_response()).read():
        match event:
            case ItemEmitted() if event.item.kind is ItemKind.TEXT:
                transcript.append(event.item.text)  # an API-error notice is a
                # signal, never a TextItem,
                # so it cannot land here
            case _ if isinstance(event, Blocking):
                blocked = event

    if blocked:
        return SdkRunResult(
            exit_code=1,
            stdout="".join(transcript),
            error=f"{blocked.reason}: {blocked.detail}",
            retry_after=getattr(blocked, "retry_after", None),  # recorded, not obeyed
        )


# ===========================================================================
# 6. Callers asking whose problem a failure is.
# ===========================================================================

match signal:
    case PersonActionRequired():
        ...  # nothing automated clears this; surface it and stop
    case HarnessActionRequired(reason=HarnessMustAct.WAIT):
        ...  # signal.retry_after says when capacity returns
    case HarnessActionRequired(reason=HarnessMustAct.RETRY):
        ...  # transient; another attempt is reasonable
    case HarnessActionRequired(reason=HarnessMustAct.ABORT):
        ...  # retrying cannot help
    case Notice(reason=WorthRecording.UNSUPPORTED_RECORD):
        ...  # schema drift, visible the first time it appears


# ===========================================================================
# 7. The usage report folds once, at the end.
# ===========================================================================


def build_usage_report(events) -> dict:
    run = collect(events)
    return {
        "schema_version": "usage/v2",
        "usage_totals": _as_dict(run.usage),
        "api_calls": run.api_calls,
        "signals": [_as_dict(s) for s in run.signals],
        # ... plus the existing keys
    }


# ===========================================================================
# 8. HATS-1967 tails the same reader — this is what the stream shape buys.
#    No special live mode: the reader that parses a finished transcript is the
#    reader that follows a running one.
# ===========================================================================


def follow(path):
    reader = ClaudeTranscriptReader(path)  # holds its own position
    while not reader.exhausted:
        yield from reader.read()  # only what was appended since
        _wait()  # a response still being written
        # has simply not produced its
        # ResponseEnded yet
