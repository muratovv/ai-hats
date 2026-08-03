#!/usr/bin/env python3
"""HATS-1407 — the Python twin of ``bypass_journal.sh``.

PreToolUse hooks are stdlib-only and run under the system interpreter, so they
cannot import ``ai_hats``; they import this sibling instead. Both writers emit
the same fields in the same order — ``tests/test_bypass_journal_contract.py``
fails if they drift.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

#: Field order of every journal line; mirrored by the shell twin's
#: AI_HATS_BYPASS_FIELDS.
FIELDS = (
    "ts",
    "event",
    "hook",
    "kind",
    "reason",
    "cmd",
    "head_before",
    "branch",
    "session_id",
    "sha",
)


def _git(*args: str) -> str:
    """Run a read-only git command; empty string when git cannot answer."""
    try:
        res = subprocess.run(  # noqa: S603 — argv is literal, never caller input
            ["git", *args],  # noqa: S607 — PATH lookup is the point in a hook
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def journal_bypass(
    kind: str,
    reason: str,
    *,
    hook: str | None = None,
    cmd: str = "",
    session_id: str = "",
) -> bool:
    """Append one bypass record. Returns False (loudly) if it could not.

    Never raises: a hook must not die because the journal is unwritable. It must
    not go quiet either — an unrecorded bypass is the defect this file removes.
    """
    hook_name = hook or Path(sys.argv[0]).name or "unknown"
    git_dir = _git("rev-parse", "--git-common-dir")
    if not git_dir:
        print(f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — no git dir", file=sys.stderr)
        return False

    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": os.environ.get("AI_HATS_HOOK_EVENT", "unknown"),
        "hook": hook_name,
        "kind": kind,
        "reason": reason,
        "cmd": cmd,
        "head_before": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "session_id": session_id or os.environ.get("AI_HATS_SESSION_ID", ""),
        "sha": "",
    }
    # A PreToolUse hook fires outside any commit, so the tool call it waved
    # through belongs to whatever HEAD is now — no post-commit stamp to wait for.
    entry["sha"] = entry["head_before"]

    path = Path(git_dir) / "ai-hats" / "bypasses.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({k: entry[k] for k in FIELDS}, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — {path}: {exc}",
            file=sys.stderr,
        )
        return False
    return True
