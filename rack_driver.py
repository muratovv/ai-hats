#!/usr/bin/env python
"""Wired-kernel driver for K6 phase B (HATS-1026).

Fills the phase-B gap finding (comparison-report.md §known gaps): the shipped
``rack`` CLI runs a PURE kernel (no subscribers), and the fully wired
assembly (``build_rack_kernel`` + ``consumer_subscribers``) has no CLI entry.
Same root resolution / actor / output surfaces as ``rack transition``;
only the kernel wiring is swapped from bare to full.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ai_hats_rack.cli import _handle_kernel_error
from ai_hats_rack.cli_common import actor, resolved_root
from ai_hats_rack.journal import JsonlJournalSink

from ai_hats.rack_consumers import consumer_subscribers
from ai_hats.rack_wiring import build_rack_kernel

USAGE = "usage: rack_driver.py transition TASK_ID TO_STATE [--force] [--reason R]"


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[0] != "transition":
        print(USAGE, file=sys.stderr)
        return 2
    task_id, to_state = argv[1], argv[2]
    force = "--force" in argv
    reason = argv[argv.index("--reason") + 1] if "--reason" in argv else ""

    caller_cwd = Path.cwd()
    env_override = os.environ.get("RACK_TASKS_DIR", "")  # same envvar the rack CLI honors
    root = resolved_root(Path(env_override) if env_override else None, caller_cwd)
    kernel = build_rack_kernel(
        root.project_dir,
        tasks_dir=root.tasks_dir,
        prefix=root.prefix,
        journal_sink=JsonlJournalSink(root.tasks_dir),
        extra_subscribers=consumer_subscribers(root.project_dir, tasks_dir=root.tasks_dir),
    )
    try:
        result = kernel.transition(
            task_id,
            to_state,
            actor=actor(),
            caller_cwd=caller_cwd,
            force=force,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 — routed to the rack CLI's typed handler
        _handle_kernel_error(exc, False)  # SystemExit(1) for known classes; unknown re-raise
        return 1
    for t in result.transitions:
        print(f"Transitioned: {t.task_id} {t.from_state} → {t.to_state}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
