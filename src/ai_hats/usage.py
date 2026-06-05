"""Per-session context-cost + usage report from a Claude Code JSONL transcript.

HATS-664 (child of HATS-663, transcript-first observability). Turns ONE Claude
Code session transcript (``~/.claude/projects/<key>/<id>.jsonl``) into a
machine-readable ``usage/v1`` report: measured always-on budget, an ordered
event timeline (skill-body loads, reference Reads, tool calls, stop-hook
firings), aggregates with tool success-rate, and sidechain linkage.

Design (per the HATS-664 brainstorm review):
- **Pure & transcript-only.** ``parse_session_usage`` takes a path and returns a
  plain ``dict`` — no live-session deps, no ``costs.py``/composer. That keeps it
  unit-testable with a fixture JSONL (dict-out assertions) and runnable
  retroactively over historical transcripts. Static ``costs.py`` cross-check
  (role-dependent) is layered on by the ``compute_usage`` Step, not here.
- **Fail-soft.** A malformed line, an unknown entry ``type``, or any per-entry
  error is recorded in ``report["flags"]`` and skipped — the parser never
  raises on transcript content. Pin/guard the observed schema as ``usage/v1``.
- **Single-session.** One transcript in, one report out. Cross-history
  aggregation is a bash sweep over many reports, not a flag here.

Token attribution is a documented heuristic: per-message ``usage`` is a per-turn
total, never per-component. A load event's ``tokens_delta`` is *reconstructed*
from the next assistant message's ``cache_creation_input_tokens`` (the freshly
loaded content entering the cached working set). It is labelled ``reconstructed``
and is ``None`` when no signal exists — never a magic ``0`` (honors
``rule_composition_value_contract §3``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "usage/v1"

# A reference Read loads skill-body depth: either a file under a ``references/``
# dir or a ``SKILL.md`` itself.
_REF_MARKERS = ("/references/", )


def _is_reference_path(file_path: str) -> bool:
    if not file_path:
        return False
    return file_path.endswith("SKILL.md") or any(m in file_path for m in _REF_MARKERS)


def _empty_report(source: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "session_id": None,
        "entry_types_seen": {},
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
        "sidechain": {"is_sidechain": False, "agent_name": None, "parent_session_id": None},
        "flags": [],
    }


def parse_session_usage(jsonl_path: str | Path) -> dict[str, Any]:
    """Parse one Claude Code JSONL transcript into a ``usage/v1`` report dict.

    Fail-soft: returns a (possibly partial) report with ``flags`` populated on
    any content problem; raises only if ``jsonl_path`` cannot be opened at all.
    """
    path = Path(jsonl_path)
    report = _empty_report(path.name)
    flags: list[str] = report["flags"]

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # Unreadable file is an infrastructure error, not transcript drift —
        # surface it but still hand back a well-formed empty report.
        flags.append(f"unreadable: {type(exc).__name__}")
        return report

    types_seen: dict[str, int] = report["entry_types_seen"]
    totals = report["usage_totals"]
    agg = report["aggregates"]
    timeline: list[dict[str, Any]] = report["timeline"]

    # Pending load events awaiting the next assistant cache_creation for
    # reconstructed token attribution.
    pending: list[dict[str, Any]] = []
    bad_lines = 0
    unknown_types: set[str] = set()

    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if not isinstance(obj, dict):
            bad_lines += 1
            continue

        try:
            _process_entry(
                obj, report, types_seen, totals, agg, timeline, pending, unknown_types,
            )
        except Exception as exc:  # fail-soft: one bad entry never sinks the parse
            flags.append(f"entry-error line {lineno}: {type(exc).__name__}")

    if bad_lines:
        flags.append(f"malformed-lines: {bad_lines}")
    if unknown_types:
        flags.append(f"unknown-entry-types: {sorted(unknown_types)}")

    # Finalize success-rate (None when no tool results — distinct from 0.0).
    results = agg["tool_results"]
    if results:
        agg["tool_success_rate"] = round(1.0 - agg["tool_errors"] / results, 4)

    return report


_KNOWN_TYPES = {
    "assistant", "user", "system", "attachment", "ai-title", "permission-mode",
    "last-prompt", "file-history-snapshot", "mode", "agent-name",
    "queue-operation", "summary",
}


def _process_entry(
    obj: dict[str, Any],
    report: dict[str, Any],
    types_seen: dict[str, int],
    totals: dict[str, int],
    agg: dict[str, Any],
    timeline: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    unknown_types: set[str],
) -> None:
    etype = obj.get("type")
    types_seen[etype] = types_seen.get(etype, 0) + 1
    if etype not in _KNOWN_TYPES:
        unknown_types.add(str(etype))

    if report["session_id"] is None and obj.get("sessionId"):
        report["session_id"] = obj["sessionId"]

    # Sidechain linkage (detect + link, no per-event merge — HATS-664 Q4).
    if obj.get("isSidechain") is True:
        report["sidechain"]["is_sidechain"] = True
        if report["sidechain"]["parent_session_id"] is None:
            report["sidechain"]["parent_session_id"] = (
                obj.get("sourceToolAssistantUUID") or obj.get("sessionId")
            )
    if etype == "agent-name":
        report["sidechain"]["is_sidechain"] = True
        name = obj.get("name") or obj.get("agentName") or (obj.get("message") or {}).get("name")
        if name:
            report["sidechain"]["agent_name"] = name

    ts = obj.get("timestamp")

    if etype == "assistant":
        _process_assistant(obj, report, totals, agg, timeline, pending, ts)
    elif etype == "user":
        _process_user(obj, agg)
    elif etype == "system":
        _process_system(obj, agg, timeline, ts)


def _process_assistant(
    obj: dict[str, Any],
    report: dict[str, Any],
    totals: dict[str, int],
    agg: dict[str, Any],
    timeline: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    ts: Any,
) -> None:
    msg = obj.get("message") or {}
    usage = msg.get("usage") or {}
    cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)

    # Always-on proxy: the FIRST assistant message's cached working set
    # (system prompt + initial turn). Measured, native — reported raw so the
    # consumer interprets; no role/costs.py needed here. ``cache_creation`` is
    # what got freshly cached this turn; ``cache_read`` is any pre-existing
    # cached context (e.g. cross-session system-prompt cache).
    if report["always_on"] is None and usage:
        report["always_on"] = {
            "first_input_tokens": int(usage.get("input_tokens", 0) or 0),
            "first_cache_creation_input_tokens": cache_creation,
            "first_cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
            "model": msg.get("model"),
            "note": (
                "measured proxy: initial cached working set "
                "(system prompt + first user turn); not role-attributed"
            ),
        }

    totals["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
    totals["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
    totals["cache_read_input_tokens"] += int(usage.get("cache_read_input_tokens", 0) or 0)
    totals["cache_creation_input_tokens"] += cache_creation

    # Reconstructed attribution (FIFO): this turn's freshly-cached tokens are
    # assigned to the oldest load event still awaiting a signal — the content
    # loaded a turn or two earlier that just entered the cached working set.
    # Events that never see a following cache_creation keep ``tokens_delta`` =
    # None (never a magic 0 — "absent" stays distinct from "measured zero").
    if cache_creation and pending:
        ev = pending.pop(0)
        ev["tokens_delta"] = cache_creation
        ev["tokens_attribution"] = "reconstructed"

    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            agg["tool_calls"] += 1
            name = block.get("name", "?")
            inp = block.get("input") or {}
            if name == "Skill":
                skill = inp.get("skill", "?")
                agg["skill_loads"][skill] = agg["skill_loads"].get(skill, 0) + 1
                ev = {"ts": ts, "kind": "skill_load", "name": skill,
                      "tokens_delta": None, "args": inp.get("args")}
                timeline.append(ev)
                pending.append(ev)
            elif name == "Read" and _is_reference_path(inp.get("file_path", "")):
                fp = inp.get("file_path", "")
                agg["reference_reads"][fp] = agg["reference_reads"].get(fp, 0) + 1
                ev = {"ts": ts, "kind": "reference_read", "name": fp, "tokens_delta": None}
                timeline.append(ev)
                pending.append(ev)
            else:
                timeline.append({"ts": ts, "kind": "tool", "name": name})


def _process_user(obj: dict[str, Any], agg: dict[str, Any]) -> None:
    msg = obj.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            agg["tool_results"] += 1
            if block.get("is_error"):
                agg["tool_errors"] += 1


def _process_system(
    obj: dict[str, Any],
    agg: dict[str, Any],
    timeline: list[dict[str, Any]],
    ts: Any,
) -> None:
    if obj.get("subtype") != "stop_hook_summary":
        return
    for hook in obj.get("hookInfos") or []:
        if not isinstance(hook, dict):
            continue
        dur = int(hook.get("durationMs", 0) or 0)
        agg["hook_firings"] += 1
        agg["hook_total_ms"] += dur
        timeline.append({
            "ts": ts, "kind": "stop_hook",
            "name": Path(str(hook.get("command", "?"))).name,
            "duration_ms": dur,
            "errors": bool(obj.get("hookErrors")),
        })


def _main(argv: list[str]) -> int:
    """Bash-composable JSON-only stdout entry: ``python -m ai_hats.usage <jsonl>``.

    Emits the ``usage/v1`` report as JSON to stdout. The single bash-composable
    primitive behind the retroactive 547-session sweep (no shipped ``--all``).
    """
    if len(argv) != 1:
        import sys
        print("usage: python -m ai_hats.usage <transcript.jsonl>", file=sys.stderr)
        return 2
    report = parse_session_usage(argv[0])
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
