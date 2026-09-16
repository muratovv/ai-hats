#!/usr/bin/env python3
"""Refuse a close unless master's own CI is green.

HATS-1877: nine defects shipped in v0.15.0; seven would have been caught by the
CI `e2e` job, red since before 2026-07-28 for an unrelated reason. Nothing
alarmed for a month. This is the alarm: the close asks GitHub what master's last
CI run concluded, and passes only on a completed green one. Everything else
refuses — a red run, a run still going, a verdict that could not be read.
Unknown is not green: a check that passes on "nobody knows yet" is the same
defect class this exists to close.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

TAG = "[master-ci]"

#: Supervisor override — the card closed is itself the fix, or the supervisor
#: takes an unread verdict on their own word. It must arrive from the launching
#: environment: a prefix on the agent's own command line is refused by safety-guard.
ENV_ALLOW_RED = "AI_HATS_RED_MASTER_ACK"

QUERY_TIMEOUT_S = 30

#: Where the bypass journal's single writer lives, relative to this script.
_JOURNAL_DIR = "../packages/ai-hats-library/src/ai_hats_library/hooks"


def _journal_allowed() -> None:
    """Record the one use of the hatch, the way every PreToolUse guard records its own.

    The flag was withheld from sub-agents and never written down; a supervisor's
    approval that leaves no line is indistinguishable afterwards from one nobody gave.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), _JOURNAL_DIR))
    try:
        from bypass_journal import journal_bypass
    except ImportError as exc:
        print(f"{TAG} bypass NOT RECORDED ({ENV_ALLOW_RED}) — {exc}", file=sys.stderr)
        return
    finally:
        sys.path.pop(0)
    journal_bypass("hatch", ENV_ALLOW_RED, hook="check_master_ci.py")


def _refuse(seen: str, *remedy: str, url: str = "") -> int:
    """Every outcome short of a completed green run ends here; the hatch is the one way past."""
    print(f"{TAG} FAIL: {seen}", file=sys.stderr)
    if url:
        print(f"{TAG}   {url}", file=sys.stderr)
    if os.environ.get(ENV_ALLOW_RED) == "1":
        print(
            f"{TAG} {ENV_ALLOW_RED}=1 — allowed anyway, on the supervisor's word.", file=sys.stderr
        )
        _journal_allowed()
        return 0
    for line in remedy:
        print(f"{TAG} {line}", file=sys.stderr)
    print(
        f"{TAG} The one way past: the supervisor sets {ENV_ALLOW_RED}=1 in the environment "
        "that launches the agent — for the card that fixes master, or on their own word. "
        "Writing it on the command line is refused (safety-guard): an approval you grant "
        "yourself is not one.",
        file=sys.stderr,
    )
    return 1


UNKNOWN = "Unknown is not green."


def _query(branch: str, workflow: str) -> list[dict] | str:
    """The run list, or a one-line reason it could not be obtained."""
    gh = shutil.which("gh")
    if gh is None:
        return "gh is not on PATH"
    cmd = [
        gh,
        "run",
        "list",
        "--branch",
        branch,
        "--workflow",
        workflow,
        "--limit",
        "1",
        "--json",
        "conclusion,status,displayTitle,url,headSha",
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=QUERY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"gh did not answer within {QUERY_TIMEOUT_S}s (offline?)"
    except OSError as exc:
        return f"gh could not be run: {exc}"
    if done.returncode != 0:
        return f"gh exited {done.returncode}: {done.stderr.strip().splitlines()[-1] if done.stderr.strip() else 'no output'}"
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        return f"gh returned output this check cannot read: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default="master", help="branch whose CI verdict to read")
    parser.add_argument("--workflow", default="ci.yml", help="workflow file whose verdict counts")
    args = parser.parse_args(argv)

    runs = _query(args.branch, args.workflow)
    if isinstance(runs, str):
        return _refuse(
            f"{args.branch}'s CI verdict could not be read — {runs}",
            f"{UNKNOWN} Restore the read (gh on PATH, authenticated, online), "
            "then run this gate again.",
        )
    if not runs:
        return _refuse(
            f"no {args.workflow} run recorded for {args.branch}",
            f"{UNKNOWN} Nothing has judged this branch yet.",
        )

    run = runs[0]
    status = run.get("status") or "unknown"
    conclusion = run.get("conclusion") or ""
    title = (run.get("displayTitle") or "").strip()
    url = run.get("url") or ""

    if status != "completed":
        return _refuse(
            f"{args.branch}'s last run is {status}, not a verdict — {title}",
            f"{UNKNOWN} Wait for that run to conclude, then run this gate again.",
            url=url,
        )

    if conclusion == "success":
        print(f"{TAG} ok: {args.branch} is green — {title}", file=sys.stderr)
        return 0

    return _refuse(
        f"{args.branch} last concluded '{conclusion}' — {title}",
        "Fix master first — this close waits on master, not on your branch.",
        "If master's redness is not yours, say so with evidence rather than "
        "asking for the flag: skill `red-attribution`.",
        url=url,
    )


if __name__ == "__main__":
    sys.exit(main())
