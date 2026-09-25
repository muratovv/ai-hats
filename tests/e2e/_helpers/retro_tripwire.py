"""Find the sessions under a directory whose finalize really started a session reviewer."""

from __future__ import annotations

from pathlib import Path

from ai_hats_observe.artifacts import RETRO_LOG


def _started_a_reviewer(action: str, detail: str) -> bool:
    # `spawn` lands only after Popen succeeded; `sync-start` opens the in-process run.
    return action == "spawn" or (action == "outcome" and detail.startswith("sync-start"))


def reviewer_spawns(root: Path) -> list[str]:
    """``<retro.log>: <line>`` for every line under ``root`` that records a started reviewer."""
    found: list[str] = []
    for log in sorted(root.rglob(RETRO_LOG)):
        for line in log.read_text(errors="replace").splitlines():
            fields = line.split("\t")
            if len(fields) >= 4 and _started_a_reviewer(fields[2], fields[3]):
                found.append(f"{log}: {line}")
    return found
