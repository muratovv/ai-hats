#!/usr/bin/env python3
"""Replay every Claude transcript on this machine through the canonical reader.

Counts only, never content: the corpus carries session ids, cwds and unredacted
prompts, so what leaves this process is events by kind and signals by reason
and raw code. Run it after a Claude Code release: ``unsupported_record`` is
what the reader now calls drift, ``raised`` is what it could not read at all.

    scripts/replay_claude_corpus.py [ROOT]      # default: ~/.claude/projects
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ai_hats_observe.canonical.signals import Signal
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


@dataclass
class Replay:
    files: int = 0
    seconds: float = 0.0
    # transcripts the reader raised on — its contract says none
    raised: list[str] = field(default_factory=list)
    kinds: Counter[str] = field(default_factory=Counter)
    # (<signal class>/<reason>, raw_code) → count
    signals: Counter[tuple[str, str]] = field(default_factory=Counter)


def replay(root: Path) -> Replay:
    """Every ``*.jsonl`` under ``root`` — sub-agent records included — read once."""
    out = Replay()
    started = time.monotonic()
    for path in sorted(root.rglob("*.jsonl")):
        out.files += 1
        try:
            for event in ClaudeTranscriptReader(path).read():
                out.kinds[type(event).__name__] += 1
                if isinstance(event, Signal):
                    reason = f"{type(event).__name__}/{event.reason.value}"
                    out.signals[(reason, event.raw_code or "")] += 1
        except Exception as exc:  # noqa: BLE001 — the raise IS the finding, reported below
            out.raised.append(f"{path.name}: {type(exc).__name__}: {exc}")
    out.seconds = time.monotonic() - started
    return out


def render(result: Replay) -> str:
    lines = [f"files={result.files} seconds={result.seconds:.0f} raised={len(result.raised)}"]
    lines.extend(f"  RAISED {entry}" for entry in result.raised)
    lines.append("events:")
    lines.extend(f"  {count:>8} {kind}" for kind, count in result.kinds.most_common())
    lines.append("signals (reason, raw_code):")
    by_count = sorted(result.signals.items(), key=lambda item: (-item[1], item[0]))
    lines.extend(f"  {count:>8} {reason:<45} {code}" for (reason, code), count in by_count)
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    root = Path(argv[1]).expanduser() if len(argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        print(f"replay_claude_corpus: no such directory: {root}", file=sys.stderr)
        return 2
    sys.stdout.write(render(replay(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
