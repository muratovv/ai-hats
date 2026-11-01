#!/usr/bin/env python3
"""HATS-1899 — refuse a tree- or branch-destroying git command in the MAIN checkout.

Edits in main are already denied (``wt_gate.py``); git state was not. So
``git reset --hard HEAD~1`` ran there and moved master by a commit. It came back
from the reflog, which was the timing rather than a property of the system.

How it happened is the part worth designing against: a ``cd`` excursion reset the
tool's working directory to the main checkout, the next git command inherited it,
and nothing in the environment contradicted the belief that the worktree was
still underneath — the wrong-place guard for this class fires only on edits.

Denies rather than asks: ``ask`` does not bind a headless run. The refused tier is
narrow on purpose — only spellings that discard uncommitted work or move a branch
ref. Kill switch: AI_HATS_WT_GIT_OFF=1. Stdlib-only (system python3 via shebang,
inline git).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# The hooks are stdlib-only, so both helpers arrive as flattened siblings.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from shell_walk import walk
except ImportError:  # the walk is this gate's eyes — it must not guess without them
    walk = None  # type: ignore[assignment]

try:
    from bypass_journal import journal_bypass, journal_catch
except ImportError:  # helper absent -> say so; never skip quietly

    def journal_bypass(kind: str, reason: str, **_kw) -> bool:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False

    def journal_catch(rule: str, verdict: str, **_kw) -> bool:
        print(
            f"[catch-journal] NOT RECORDED ({rule}: {verdict}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False


_KILL_SWITCH = "AI_HATS_WT_GIT_OFF"

#: Cheap pre-filter: nothing expensive runs until the word `git` appears at all.
_GIT_RE = re.compile(r"(^|[\s;|&])git([\s]|$)")


def _git_location(directory: str) -> str:
    """'main' | 'linked' | 'nongit' for ``directory``.

    'main' iff a work tree whose --git-dir equals --git-common-dir. Any error is
    'nongit', which is silent — a gate that cannot tell where it stands must not
    refuse. Mirrors ``wt_gate._git_info`` inline: the hook runs under the system
    interpreter, without ai_hats."""
    try:
        result = subprocess.run(
            [  # noqa: S607
                "git",
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
                "--git-common-dir",
            ],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return "nongit"
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) != 2:
        return "nongit"
    git_dir, common_dir = lines
    return "linked" if Path(git_dir).resolve() != Path(common_dir).resolve() else "main"


def _peel_globals(args: list[str], cwd: Path) -> tuple[Path, list[str]]:
    """Git's own options come before the subcommand; ``-C`` moves where it acts.

    ``-C`` is the spelling in use and it stacks: each is applied relative to the
    last. Reading it is what stops ``git -C <main> reset --hard`` from being
    judged against the worktree the agent happens to stand in."""
    i = 0
    while i < len(args):
        if args[i] == "-C" and i + 1 < len(args):
            try:
                cwd = (cwd / args[i + 1]).resolve()
            except (OSError, ValueError):
                return cwd, []
            i += 2
            continue
        if args[i] == "-c" and i + 1 < len(args):
            i += 2
            continue
        if args[i].startswith("-"):
            i += 1
            continue
        break
    return cwd, args[i:]


def _positionals(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


def _refusal(sub: str, rest: list[str], cwd: Path) -> str | None:
    """Why this git command must not run here, or None to stay out of the way.

    Every branch below is the narrow tier: it destroys uncommitted work or moves
    a branch ref. Anything git itself already refuses (``checkout <branch>`` with
    dirty state, ``branch -d`` on unmerged) is left alone — a second opinion on a
    command that is already safe is how a gate gets switched off."""
    # `--` means opposite things here: after it `reset` takes paths (safe, it
    # unstages) while `checkout` takes paths to overwrite. So the split is read
    # per subcommand rather than once.
    if "--" in rest:
        cut = rest.index("--")
        before, after = rest[:cut], rest[cut + 1 :]
    else:
        before, after = rest, []

    if sub == "reset":
        for flag in ("--hard", "--merge", "--keep"):
            if flag in rest:
                return f"`git reset {flag}` throws away uncommitted work"
        # `git reset <commit-ish>` moves the branch ref; `git reset <path>` and
        # `git reset -- <path>` only unstage, and os.path decides which it is.
        for arg in _positionals(before):
            if not (cwd / arg).exists():
                return f"`git reset {arg}` moves the branch ref"
        return None

    if sub == "checkout":
        if "-f" in rest or "--force" in rest:
            return "`git checkout -f` throws away uncommitted work"
        if after:
            return "`git checkout -- <path>` overwrites the file from the index"
        for arg in _positionals(before):
            if (cwd / arg).exists():
                return f"`git checkout {arg}` overwrites that path from the index"
        return None

    if sub == "switch":
        for flag in ("-f", "--force", "--discard-changes"):
            if flag in rest:
                return f"`git switch {flag}` throws away uncommitted work"
        return None

    if sub == "restore":
        if "--staged" in rest and "--worktree" not in rest:
            return None  # unstages only; the working tree is untouched
        return "`git restore` overwrites the working tree from the index"

    if sub == "branch":
        for flag in ("-f", "--force", "-D", "-M"):
            if flag in rest:
                return f"`git branch {flag}` moves or deletes a branch ref"
        return None

    if sub == "clean":
        for arg in rest:
            if arg == "--force" or (
                arg.startswith("-") and not arg.startswith("--") and "f" in arg
            ):
                return "`git clean -f` deletes untracked files outright"
        return None

    return None


def _sole_live_worktree(cwd: Path) -> Path | None:
    """The one linked worktree still on disk, when there is exactly one.

    Naming the worktree the agent meant is only honest when the answer is
    unambiguous; a checkout carrying a dozen stale registrations has no such
    answer, and guessing one would send the retry to the wrong place."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],  # noqa: S607
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    paths = [
        Path(line.split(" ", 1)[1])
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    ]
    live = [p for p in paths[1:] if p.is_dir()]  # paths[0] is the main checkout
    return live[0] if len(live) == 1 else None


def _reason(why: str, cwd: Path) -> str:
    sole = _sole_live_worktree(cwd)
    where = (
        f"You are probably after the worktree at {sole}."
        if sole is not None
        else "Run `ai-hats wt status` to find the worktree you meant."
    )
    return (
        f"GUARDRAIL (worktree-isolation): blocked — {why}, and you are standing in the "
        f"MAIN checkout ({cwd}).\n"
        f"{where}\n"
        "Edits here are already denied; this is the same rule for git state. Re-run it "
        "from inside the worktree, or with `git -C <worktree>`.\n"
        f"Kill switch (supervisor only): {_KILL_SWITCH}=1"
    )


def _finding(command: str, cwd: Path) -> tuple[str, Path] | None:
    """The first destructive git command aimed at the main checkout, if any."""
    steps = walk(command, cwd)
    if steps is None:
        return None  # unparsable, or a cd we lost -> prove nothing
    for segment, effective_cwd, _path_override in steps:
        if Path(segment[0]).name != "git" or len(segment) < 2:
            continue
        target, args = _peel_globals(segment[1:], effective_cwd)
        if not args:
            continue
        why = _refusal(args[0], args[1:], target)
        if why is None:
            continue
        if _git_location(str(target)) == "main":
            return why, target
    return None


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        journal_bypass("hatch", _KILL_SWITCH, hook="wt_git_gate.py")
        return 0

    if walk is None:
        journal_bypass("fail-open", "shell_walk.py missing", hook="wt_git_gate.py")
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook="wt_git_gate.py")
        return 0

    tool_input = payload.get("tool_input")
    command = (tool_input or {}).get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not _GIT_RE.search(command):
        return 0  # no git in the line -> nothing expensive happens

    cwd_raw = (payload.get("cwd") or "").strip() or os.getcwd()
    try:
        cwd = Path(cwd_raw).resolve()
    except (OSError, ValueError):
        return 0

    finding = _finding(command, cwd)
    if finding is None:
        return 0
    why, target = finding

    journal_catch("worktree-isolation", "deny", hook="wt_git_gate.py", cmd=command)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _reason(why, target),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
