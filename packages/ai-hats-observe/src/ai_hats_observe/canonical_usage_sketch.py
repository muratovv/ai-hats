"""DRAFT (HATS-1966) — how `canonical` gets used, per consumer.

Illustrative only: this file is a review artifact, not a module. It will be
deleted once the call sites it sketches are real. Read it top to bottom — each
block is one consumer and shows the before/after where the behaviour changes.
"""

from __future__ import annotations

from ai_hats_observe.canonical import (
    VIEW_AB,
    VIEW_FULL,
    VIEW_JUDGE,
    CanonicalSession,
    Completion,
    ItemKind,
    Severity,
    SignalKind,
    from_jsonl,
    from_sdk,
)

# ===========================================================================
# 1. The parser produces it. The legacy shape is DERIVED, so `audit.md` keeps
#    working while the new model becomes the source of truth (expand-contract).
# ===========================================================================


class ClaudeParser:
    def parse(self, jsonl_path, trace_path):
        session = from_jsonl(_read_records(jsonl_path))
        return ParsedTranscript(
            # legacy fields, now computed off the canonical model
            turns=[_legacy_turn(ex) for ex in session.exchanges],
            model_stats=_legacy_model_stats(session),
            agg_usage=_as_dict(session.usage),      # counted once per response
            flags=[],
            # new fields
            exchanges=session.exchanges,
            signals=session.signals,
        )


# ===========================================================================
# 2. The token fix, concretely. This is the whole 2.61x defect.
# ===========================================================================

# BEFORE — every fragment of one API call adds its (identical) usage again:
#     for record in records:
#         agg["input_tokens"] += record["message"]["usage"]["input_tokens"]
#     # one response spanning 3 records is counted 3 times
#
# AFTER — usage rides the Response, which exists once per requestId:
total = session.usage                    # -> Usage(input_tokens=..., ...)
per_call = [r.usage for ex in session.exchanges for r in ex.responses]
n_api_calls = len(per_call)              # real calls, not fragments


# ===========================================================================
# 3. AuditWriter renders the full view — thinking included.
# ===========================================================================


def render_audit(session: CanonicalSession) -> str:
    lines = []
    for ex in VIEW_FULL.apply(session.exchanges):
        lines.append(f"## {ex.ts}")
        if ex.prompt:
            lines.append(f"👤 {ex.prompt}")
        for response in ex.responses:
            for item in response.items:
                match item.kind:
                    case ItemKind.THINKING:
                        lines.append(f"💭 {item.text}")
                    case ItemKind.TOOL_CALL:
                        lines.append(f"🔧 {item.name}({item.input})")
                    case ItemKind.TOOL_RESULT:
                        # today this never reaches audit.md at all
                        mark = "✅" if item.ok else "❌"
                        lines.append(f"{mark} -> {item.content}")
                    case ItemKind.TEXT:
                        lines.append(f"👾 {item.text}")
            if response.completion is not Completion.COMPLETE:
                lines.append(f"⚠️  response {response.completion}")

    # the health axis renders once, at the end — a run killed by the platform
    # stops looking like a run that simply finished early
    for signal in session.signals:
        lines.append(f"[{signal.severity}] {signal.kind}: {signal.detail}")
    return "\n".join(lines)


# ===========================================================================
# 4. Per-consumer projections — the "smarter storage" requirement.
# ===========================================================================

judge_input = VIEW_JUDGE.apply(session.exchanges)   # no ThinkingItem: judge scores
                                                    # the output, not the reasoning
ab_input = VIEW_AB.apply(session.exchanges)         # thinking kept: an A/B run
                                                    # compares *how* it got there


# ===========================================================================
# 5. sdk_runner — two changes.
# ===========================================================================


async def drain_one_turn(client, message):
    messages = []
    async for msg in client.receive_response():
        messages.append(msg)

    session = from_sdk(messages)

    # (a) the transcript no longer carries API-error text. Today a
    #     "You've hit your monthly spend limit" TextBlock becomes the agent's
    #     answer — that happened in 15 runs in the corpus.
    transcript = "".join(r.text for ex in session.exchanges for r in ex.responses)

    # (b) the run's death is legible instead of silent
    if killed := session.failed:
        return SdkRunResult(
            exit_code=SDK_EXIT_ERROR,
            stdout=transcript,
            error=f"{killed.kind}: {killed.detail}",
            # recorded, not acted on — retry policy is a different card
            retry_after=killed.retry_after,
        )


# ===========================================================================
# 6. Callers that just want a yes/no on run health.
# ===========================================================================

if signal := session.failed:
    match signal.kind:
        case SignalKind.AUTH | SignalKind.BILLING:
            ...   # a human has to act
        case SignalKind.QUOTA | SignalKind.SERVICE:
            ...   # signal.transient is True; signal.retry_after may be set
        case SignalKind.INVALID_REQUEST:
            ...   # our bug

# and the drift detector becomes a real signal again: today 12 of 23 record
# types are unclassified, so `unknown-entry-types` fires on 59.4% of sessions
drifted = [s for s in session.signals if s.kind is SignalKind.UNSUPPORTED]


# ===========================================================================
# 7. The usage report (new schema version) is a projection too.
# ===========================================================================


def build_usage_report(session: CanonicalSession) -> dict:
    return {
        "schema_version": "usage/v2",
        "usage_totals": _as_dict(session.usage),
        "api_calls": sum(len(ex.responses) for ex in session.exchanges),
        "signals": [
            {
                "kind": s.kind,
                "severity": s.severity,
                "ts": s.ts,
                "retry_after": s.retry_after,
                "raw_code": s.raw_code,
            }
            for s in session.signals
        ],
        # ... plus the existing v1 keys, with entry_types_seen now complete
    }


# ===========================================================================
# 8. HATS-1967 later tails the same model — IN_FLIGHT is the hinge.
# ===========================================================================


def tail(path):
    for batch in _follow(path):
        session = from_jsonl(batch)
        for ex in session.exchanges:
            for response in ex.responses:
                if response.completion is Completion.IN_FLIGHT:
                    continue          # still being written; wait for its terminal fragment
                yield response
