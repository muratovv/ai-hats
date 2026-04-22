#!/usr/bin/env python3
"""One-shot retro coverage diagnostic (HATS-158).

Walks `.gitlog/session_*/` directories, re-runs the current `should_run()`
policy against each session's metrics.json, and classifies outcomes into
buckets:

  - `retro_present`      — retro file exists; did the job.
  - `would_skip(<reason>)` — current policy would skip; persistent skip.
  - `would_run_missing`   — policy says run, but no retro file → builder
                           or hook failed silently (not recoverable from
                           logs, but known-missing).
  - `missing_metrics`     — no metrics.json; can't decide retroactively.

Run from project root:
    python scripts/diagnose_retro_coverage.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from ai_hats.retro.auto_retro import should_run


def classify(project_dir: Path, session_dir: Path) -> tuple[str, int, int]:
    """Return (bucket, turns, tool_calls) for a session."""
    sid = session_dir.name.removeprefix("session_")

    # Retro file present?
    retros_dir = project_dir / ".agent" / "retrospectives" / "sessions"
    for mode in ("llm", "programmatic"):
        if (retros_dir / mode / f"{sid}.md").exists():
            turns, tool_calls = _read_metrics(session_dir / "metrics.json")
            return "retro_present", turns, tool_calls

    metrics_path = session_dir / "metrics.json"
    if not metrics_path.exists():
        return "missing_metrics", 0, 0

    action, reason = should_run(project_dir / "ai-hats.yaml", metrics_path)
    turns, tool_calls = _read_metrics(metrics_path)

    if action == "skip":
        # Normalize threshold reasons to one bucket for aggregation.
        if "below threshold" in reason:
            return "would_skip(below_threshold)", turns, tool_calls
        if "unreadable" in reason:
            return "would_skip(bad_metrics)", turns, tool_calls
        if "not found" in reason:
            return "would_skip(no_metrics)", turns, tool_calls
        return f"would_skip({reason})", turns, tool_calls

    if action == "hint":
        return "would_hint", turns, tool_calls

    # action == "run" but retro file not present → silent fail in old run.
    return "would_run_missing", turns, tool_calls


def _read_metrics(path: Path) -> tuple[int, int]:
    try:
        m = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0, 0
    return int(m.get("turns", 0) or 0), int(m.get("tool_calls", 0) or 0)


def main() -> None:
    project_dir = Path.cwd()
    gitlog = project_dir / ".gitlog"
    if not gitlog.is_dir():
        print(f"No .gitlog/ in {project_dir}")
        return

    sessions = sorted(p for p in gitlog.iterdir() if p.is_dir() and p.name.startswith("session_"))
    buckets: Counter[str] = Counter()
    by_bucket_ids: dict[str, list[tuple[str, int, int]]] = defaultdict(list)

    for sd in sessions:
        bucket, turns, tc = classify(project_dir, sd)
        buckets[bucket] += 1
        sid = sd.name.removeprefix("session_")
        by_bucket_ids[bucket].append((sid, turns, tc))

    total = len(sessions)
    print(f"=== retro coverage diagnostic — {total} sessions ===\n")
    for bucket, count in buckets.most_common():
        pct = 100 * count / total if total else 0
        print(f"  {bucket:40s} {count:4d}  ({pct:5.1f}%)")

    # Highlight would_run_missing — substantive sessions that silently
    # lost their retro.
    missing = by_bucket_ids.get("would_run_missing", [])
    if missing:
        print(f"\n=== would_run_missing — {len(missing)} sessions (investigate) ===")
        for sid, turns, tc in sorted(missing, key=lambda x: (-x[1], -x[2])):
            print(f"  {sid}  turns={turns:4d}  tool_calls={tc:4d}")


if __name__ == "__main__":
    main()
