#!/usr/bin/env python3
"""Read master's last CI verdict: a stage that refuses on red, or a notice that never does.

Nine defects shipped while the CI `e2e` job had been red for a month for an
unrelated reason — the one arm that could see them was never read. Bare, this is
the `master-ci` stage: run by hand, red on a non-success. With `--notice` it is
what the push road prints: the same verdict, exit 0 whatever it is, because a
push is how master gets fixed and a refusal there would block the fix.

A skip is always announced: a check that quietly does nothing is the same defect.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

TAG = "[master-ci]"

QUERY_TIMEOUT_S = 30
#: A pre-push hook shares the push's own budget: GitHub drops the connection
#: after ~30s idle, so the notice must answer well inside it.
NOTICE_TIMEOUT_S = 10


def _skip(reason: str) -> int:
    print(f"{TAG} SKIPPED: {reason}", file=sys.stderr)
    print(
        f"{TAG} master's CI verdict is unknown to this run — it was not checked.", file=sys.stderr
    )
    return 0


def _query(branch: str, workflow: str, timeout_s: int) -> list[dict] | str:
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
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return f"gh did not answer within {timeout_s}s (offline?)"
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
    parser.add_argument(
        "--notice",
        action="store_true",
        help="report the verdict and exit 0 whatever it is (the push road)",
    )
    args = parser.parse_args(argv)

    runs = _query(args.branch, args.workflow, NOTICE_TIMEOUT_S if args.notice else QUERY_TIMEOUT_S)
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
    if args.notice:
        print(
            f"{TAG} not refusing: this push is what re-runs it. Read that run before "
            f"you rely on {args.branch} being green.",
            file=sys.stderr,
        )
        return 0
    print(
        f"{TAG} Fix master first — this stage waits on master, not on your branch.",
        file=sys.stderr,
    )
    print(
        f"{TAG} If master's redness is not yours, say so with evidence: skill `red-attribution`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
