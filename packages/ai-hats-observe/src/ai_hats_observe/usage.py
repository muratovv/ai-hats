"""Per-session context-cost + usage report from a Claude Code JSONL transcript.

HATS-664 produced ``usage/v1`` by reading each JSONL record as a message.
HATS-1966 S5 rebuilds it on the canonical model: ``ClaudeTranscriptReader``
turns the transcript into events and ``views.collect`` folds them, so the report
inherits the model's counting rules instead of re-deriving them.

What ``usage/v2`` changes (R4, R7 — historical ``usage/v1`` files are left as
they are; the schema version is what tells a reader which rule produced one):

- **Token totals are per API call, not per record.** A record is a *fragment*
  of a call and every fragment repeats a byte-identical ``usage``; summing them
  inflated totals 2.61x (12,122,477 tokens vs 4,646,373 over 700 transcripts).
- **``api_calls``** — inference calls, which is what cost is proportional to.
  ``aggregates.tool_calls`` still counts tool invocations; neither counts records.
- **``signals``** — the run-health axis: whether the run was rate-limited, had
  to reauthenticate, was compacted, or hit a record we do not model.
- **``entry_types_seen``** is complete: every one of the 23 record types the
  corpus produces is classified, so ``unknown-entry-types`` means drift again
  rather than firing on 59.4% of sessions.

``flags`` keeps its ``usage/v1`` meaning — parse quality only (unreadable file,
malformed line, unknown record type). Run health lives in ``signals`` and the
two must not be folded together: a clean parse of a killed run has no flags.

Still pure and fail-soft: a path in, a plain ``dict`` out, no session deps, and
transcript content never raises.
"""  # comment-length: allow — the schema diff is the contract of this module

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .canonical.events import Event, ItemEmitted, ResponseEnded, ResponseStarted
from .canonical.types import ItemKind, Usage
from .canonical.views import Collected, collect
from .event_log import signal_fields

SCHEMA_VERSION = "usage/v2"

# A reference Read loads skill-body depth: either a file under a ``references/``
# dir or a ``SKILL.md`` itself.
_REF_MARKERS = ("/references/",)


def _is_reference_path(file_path: str) -> bool:
    if not file_path:
        return False
    return file_path.endswith("SKILL.md") or any(m in file_path for m in _REF_MARKERS)


def empty_usage_report(source: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "session_id": None,
        # ai-hats session metadata — NOT transcript-derived, so the pure parser
        # leaves it null; ``compute_usage`` fills it from ``metrics.json`` so the
        # report self-describes which composition produced this cost.
        "role": None,
        "provider": None,
        "exit_code": None,
        "entry_types_seen": {},
        # Inference calls. A transcript record is a fragment of one, so this is
        # the only count cost is proportional to.
        "api_calls": 0,
        "usage_totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "always_on": None,
        "timeline": [],
        "aggregates": {
            "skill_loads": {},
            "reference_reads": {},
            "tool_calls": 0,
            "tool_results": 0,
            "tool_errors": 0,
            "tool_success_rate": None,
            "hook_firings": 0,
            "hook_total_ms": 0,
        },
        # Run health, one entry per signal: what happened, whose obligation it
        # is, and the surface's own code. Separate axis from ``flags``.
        "signals": [],
        "sidechain": {"is_sidechain": False, "agent_name": None, "parent_session_id": None},
        "flags": [],
    }


def parse_session_usage(jsonl_path: str | Path) -> dict[str, Any]:
    """Parse one Claude Code JSONL transcript into a ``usage/v2`` report dict.

    Two passes, because they answer different questions: the canonical reader
    says what the *run* did — calls, cost, items, signals — while the record
    pass says what the *file* is, down to the stop-hook timings the event model
    deliberately stays silent on (harness bookkeeping is not run health).

    Fail-soft: returns a (possibly partial) report with ``flags`` populated on
    any content problem; raises only if ``jsonl_path`` cannot be opened at all.
    """
    # Imported here, not at module scope: ``parsers/__init__`` imports the trace
    # parser, which imports this module — a top-level import would close a cycle.
    from .parsers.claude_events import ClaudeTranscriptReader

    path = Path(jsonl_path)
    report = empty_usage_report(path.name)

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # Unreadable file is an infrastructure error, not transcript drift —
        # surface it but still hand back a well-formed empty report.
        report["flags"].append(f"unreadable: {type(exc).__name__}")
        return report

    _read_records(raw, report)

    events = list(ClaudeTranscriptReader(path).read())
    _fold_run(collect(events), report)
    _walk_timeline(events, report)
    # The two passes stamp their own events; a report is read as a chronology.
    report["timeline"].sort(key=lambda event: event.get("ts") or "")

    return report


# --- the run, as the canonical model counts it -----------------------------


def _fold_run(session: Collected, report: dict[str, Any]) -> None:
    """Totals, calls, signals and tool outcomes, straight off the fold."""
    totals = report["usage_totals"]
    total = session.usage
    totals["input_tokens"] = total.input_tokens
    totals["output_tokens"] = total.output_tokens
    totals["cache_read_input_tokens"] = total.cache_read_input_tokens
    totals["cache_creation_input_tokens"] = total.cache_creation_input_tokens

    report["api_calls"] = session.api_calls
    report["signals"] = [signal_fields(signal) for signal in session.signals]

    agg = report["aggregates"]
    for response in session.responses:
        if report["always_on"] is None and response.usage != Usage():
            report["always_on"] = _always_on(response)
        agg["tool_calls"] += sum(1 for item in response.items if item.kind is ItemKind.TOOL_CALL)
        agg["tool_results"] += len(response.results)
        agg["tool_errors"] += sum(1 for result in response.results if not result.ok)

    # None when no tool results — distinct from a measured 0.0.
    if agg["tool_results"]:
        agg["tool_success_rate"] = round(1.0 - agg["tool_errors"] / agg["tool_results"], 4)


def _always_on(response: Any) -> dict[str, Any]:
    """Measured proxy: the first call's cached working set (system prompt +
    first user turn). Reported raw — no role attribution, no costs.py."""
    usage = response.usage
    return {
        "first_input_tokens": usage.input_tokens,
        "first_cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "first_cache_read_input_tokens": usage.cache_read_input_tokens,
        "model": response.model,
        "note": (
            "measured proxy: initial cached working set "
            "(system prompt + first user turn); not role-attributed"
        ),
    }


def _walk_timeline(events: Iterable[Event], report: dict[str, Any]) -> None:
    """The ordered narrative — built off the stream, not the fold, because
    ``collect`` keeps no timestamps and a timeline is nothing without them.

    Token attribution stays the documented ``reconstructed`` heuristic: a load
    event is credited with the *next* call's ``cache_creation`` — the freshly
    loaded content entering the cached working set. Unattributable events keep
    ``tokens_delta = None``, never a magic 0.
    """
    agg = report["aggregates"]
    timeline: list[dict[str, Any]] = report["timeline"]
    # loads from calls already billed, oldest first, and the ones this call made
    awaiting: list[dict[str, Any]] = []
    in_flight: list[dict[str, Any]] = []

    for event in events:
        match event:
            case ResponseStarted():
                in_flight = []
            case ItemEmitted() if event.item.kind is ItemKind.TOOL_CALL:
                entry = _tool_event(event.item, agg, event.ts)
                timeline.append(entry)
                if entry["kind"] != "tool":
                    in_flight.append(entry)
            case ResponseEnded():
                cached = event.usage.cache_creation_input_tokens
                if cached and awaiting:
                    attributed = awaiting.pop(0)
                    attributed["tokens_delta"] = cached
                    attributed["tokens_attribution"] = "reconstructed"
                awaiting.extend(in_flight)
                in_flight = []


def _tool_event(item: Any, agg: dict[str, Any], ts: Any) -> dict[str, Any]:
    """One tool call as a timeline entry; skill bodies and reference reads are
    the two that load context, so they are also counted and await attribution."""
    # A tool input is whatever the model sent, so nothing here trusts its type:
    # a stringly-odd argument must not cost the caller its whole report.
    inputs = item.input or {}
    if item.name == "Skill":
        skill = str(inputs.get("skill", "?"))
        agg["skill_loads"][skill] = agg["skill_loads"].get(skill, 0) + 1
        return {
            "ts": ts,
            "kind": "skill_load",
            "name": skill,
            "tokens_delta": None,
            "args": inputs.get("args"),
        }
    file_path = inputs.get("file_path") if item.name == "Read" else None
    if isinstance(file_path, str) and _is_reference_path(file_path):
        agg["reference_reads"][file_path] = agg["reference_reads"].get(file_path, 0) + 1
        return {"ts": ts, "kind": "reference_read", "name": file_path, "tokens_delta": None}
    return {"ts": ts, "kind": "tool", "name": item.name}


# --- the file, as a pile of records ----------------------------------------


def _read_records(raw: str, report: dict[str, Any]) -> None:
    """What only the records can say: which types occur, which session this is,
    whether it is a sidechain, and what the stop hooks cost."""
    from .parsers.claude_events import KNOWN_RECORD_TYPES

    types_seen: dict[str, int] = report["entry_types_seen"]
    flags: list[str] = report["flags"]
    malformed = 0
    unknown: set[str] = set()

    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        record = _record(line)
        if record is None:
            malformed += 1
            continue
        etype = str(record.get("type"))
        types_seen[etype] = types_seen.get(etype, 0) + 1
        if etype not in KNOWN_RECORD_TYPES:
            unknown.add(etype)
        try:
            _read_record(record, etype, report)
        except Exception as exc:  # fail-soft: one bad record never sinks the parse
            flags.append(f"entry-error line {lineno}: {type(exc).__name__}")

    if malformed:
        flags.append(f"malformed-lines: {malformed}")
    if unknown:
        flags.append(f"unknown-entry-types: {sorted(unknown)}")


def _read_record(record: dict[str, Any], etype: str, report: dict[str, Any]) -> None:
    if report["session_id"] is None and record.get("sessionId"):
        report["session_id"] = record["sessionId"]

    # Sidechain linkage (detect + link, no per-event merge — HATS-664 Q4).
    sidechain = report["sidechain"]
    if record.get("isSidechain") is True:
        sidechain["is_sidechain"] = True
        if sidechain["parent_session_id"] is None:
            sidechain["parent_session_id"] = record.get("sourceToolAssistantUUID") or record.get(
                "sessionId"
            )
    if etype == "agent-name":
        sidechain["is_sidechain"] = True
        name = (
            record.get("name")
            or record.get("agentName")
            or (record.get("message") or {}).get("name")
        )
        if name:
            sidechain["agent_name"] = name

    if etype == "system" and record.get("subtype") == "stop_hook_summary":
        _read_hooks(record, report)


def _read_hooks(record: dict[str, Any], report: dict[str, Any]) -> None:
    """Stop-hook timings. Harness bookkeeping, not run health — which is why the
    canonical reader stays silent on it and this is read off the record."""
    agg = report["aggregates"]
    ts = record.get("timestamp")
    for hook in record.get("hookInfos") or []:
        if not isinstance(hook, dict):
            continue
        duration = int(hook.get("durationMs", 0) or 0)
        agg["hook_firings"] += 1
        agg["hook_total_ms"] += duration
        report["timeline"].append(
            {
                "ts": ts,
                "kind": "stop_hook",
                "name": Path(str(hook.get("command", "?"))).name,
                "duration_ms": duration,
                "errors": bool(record.get("hookErrors")),
            }
        )


def _record(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _main(argv: list[str]) -> int:
    """Bash-composable JSON-only stdout entry: ``python -m ai_hats_observe.usage <jsonl>``.

    Emits the ``usage/v2`` report as JSON to stdout. The single bash-composable
    primitive behind a retroactive sweep (no shipped ``--all``).
    """
    if len(argv) != 1:
        import sys

        print("usage: python -m ai_hats_observe.usage <transcript.jsonl>", file=sys.stderr)
        return 2
    report = parse_session_usage(argv[0])
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
