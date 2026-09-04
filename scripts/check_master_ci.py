#!/usr/bin/env python3
"""Refuse a close while master's own CI is red.

HATS-1877: nine defects shipped in v0.15.0. Seven of them would have been
caught by the CI `e2e` job, which had been failing since before 2026-07-28 for
an unrelated reason. Nothing alarmed for a month, so the one arm that could see
them was never read. This is the alarm: the gate that closes a card asks GitHub
what master's last CI run concluded, and refuses on a red one.

A skip is always announced. A check that quietly does nothing is the same
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

#: Supervisor override, for the one legitimate case: the card being closed is
#: itself the fix for the redness.
ENV_ALLOW_RED = "AI_HATS_RED_MASTER_ACK"

QUERY_TIMEOUT_S = 30


def _skip(reason: str) -> int:
    print(f"{TAG} SKIPPED: {reason}", file=sys.stderr)
    print(
        f"{TAG} master's CI verdict is unknown to this run — it was not checked.", file=sys.stderr
    )
    return 0


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
        return _skip(runs)
    if not runs:
        return _skip(f"no {args.workflow} run recorded for {args.branch}")

    run = runs[0]
    status = run.get("status") or "unknown"
    conclusion = run.get("conclusion") or ""
    title = (run.get("displayTitle") or "").strip()
    url = run.get("url") or ""

    if status != "completed":
        print(f"{TAG} {args.branch}: last run is {status} — {title}", file=sys.stderr)
        print(f"{TAG} no verdict yet; not refusing on an unfinished run. {url}", file=sys.stderr)
        return 0

    if conclusion == "success":
        print(f"{TAG} ok: {args.branch} is green — {title}", file=sys.stderr)
        return 0

    print(f"{TAG} FAIL: {args.branch} last concluded '{conclusion}' — {title}", file=sys.stderr)
    print(f"{TAG}   {url}", file=sys.stderr)
    if os.environ.get(ENV_ALLOW_RED) == "1":
        print(
            f"{TAG} {ENV_ALLOW_RED}=1 — allowed anyway, on the supervisor's word.", file=sys.stderr
        )
        return 0
    print(
        f"{TAG} Fix master first. If THIS card is that fix, re-run with {ENV_ALLOW_RED}=1.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
