#!/usr/bin/env python3
"""Read master's last CI verdict: a stage green only on a completed green run, or a notice that never refuses.

Nine defects shipped while the CI `e2e` job had been red for a month for an
unrelated reason — the one arm that could see them was never read. Bare, this is
the `master-ci` stage, run by hand: it passes only on a completed green run and
refuses everything else — a red run, a run still going, a verdict it could not
read. Unknown is not green. With `--notice` it is what the push road prints: the
same verdict, exit 0 whatever it is, because a push is how master gets fixed and
a refusal there would block the fix.
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

UNKNOWN = "Unknown is not green."


def _refuse(seen: str, *remedy: str, url: str = "", notice: bool) -> int:
    """Every outcome short of a completed green run ends here; a notice says it and lets go."""
    print(f"{TAG} FAIL: {seen}", file=sys.stderr)
    if url:
        print(f"{TAG}   {url}", file=sys.stderr)
    if notice:
        print(
            f"{TAG} not refusing: a notice tells, and the push is how master gets fixed. "
            "Read the run above before you rely on master being green.",
            file=sys.stderr,
        )
        return 0
    for line in remedy:
        print(f"{TAG} {line}", file=sys.stderr)
    return 1


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
    notice = args.notice

    runs = _query(args.branch, args.workflow, NOTICE_TIMEOUT_S if notice else QUERY_TIMEOUT_S)
    if isinstance(runs, str):
        return _refuse(
            f"{args.branch}'s CI verdict could not be read — {runs}",
            f"{UNKNOWN} Restore the read (gh on PATH, authenticated, online), "
            "then run this stage again.",
            notice=notice,
        )
    if not runs:
        return _refuse(
            f"no {args.workflow} run recorded for {args.branch}",
            f"{UNKNOWN} Nothing has judged this branch yet.",
            notice=notice,
        )

    run = runs[0]
    status = run.get("status") or "unknown"
    conclusion = run.get("conclusion") or ""
    title = (run.get("displayTitle") or "").strip()
    url = run.get("url") or ""

    if status != "completed":
        return _refuse(
            f"{args.branch}'s last run is {status}, not a verdict — {title}",
            f"{UNKNOWN} Wait for that run to conclude, then run this stage again.",
            url=url,
            notice=notice,
        )

    if conclusion == "success":
        print(f"{TAG} ok: {args.branch} is green — {title}", file=sys.stderr)
        return 0

    return _refuse(
        f"{args.branch} last concluded '{conclusion}' — {title}",
        "Fix master first — this stage waits on master, not on your branch.",
        "If master's redness is not yours, say so with evidence: skill `red-attribution`.",
        url=url,
        notice=notice,
    )


if __name__ == "__main__":
    sys.exit(main())
