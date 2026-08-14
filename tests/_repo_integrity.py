"""HATS-887 — pure snapshot/diff of a repo's test-mutable surface, for the
session-scoped real-repo integrity tripwire.

Watches the checked-out HEAD + THIS worktree's index (``git ls-files -s``
digest), NOT all refs: other agents in a shared clone legitimately move sibling
branches, which an all-refs snapshot would misreport as a test mutation.

HATS-1675: a movement is all this observes — never its author, so the snapshot
also carries the top of HEAD's reflog as quotable evidence.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_NOT_A_REPO = "<not-a-git-repo>"
_REFLOG_WINDOW = 20


@dataclass(frozen=True)
class RepoState:
    """Value snapshot of a repo's test-mutable surface. Compares by value."""

    head: str
    index_digest: str  # sha256 of `git ls-files -s`; "" when not a repo
    tracked_count: int | None  # kept for a human-readable delta message
    reflog: tuple[str, ...] = ()  # top HEAD reflog lines, newest first; () when unreadable

    @property
    def is_repo(self) -> bool:
        return self.head != _NOT_A_REPO


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Strip GIT_* so an ambient GIT_DIR (merge-smoke's `git merge` exports it at
    # the real repo) can't retarget the snapshot off `root` — the HATS-886 vector.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        return subprocess.run(
            ["git", *args], cwd=str(root), env=env, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        # No `git` on PATH (e.g. the empty-PATH offline subprocess in
        # test_venv_strict_mode); the tripwire is session-autouse so a raise
        # would crash the subprocess. Signal 127 -> snapshot_repo degrades.
        return subprocess.CompletedProcess(["git", *args], returncode=127, stdout="", stderr="")


def snapshot_repo(root: Path) -> RepoState:
    """Snapshot ``root``'s HEAD + index fingerprint.

    Degrades to a not-a-repo sentinel (never raises) when ``root`` has no
    ``.git`` (e.g. a source dir unpacked from an sdist) or when the ``git``
    binary is unavailable (e.g. the empty-PATH offline subprocess in
    ``test_venv_strict_mode``).
    """
    if not (root / ".git").exists():
        return RepoState(head=_NOT_A_REPO, index_digest="", tracked_count=None)

    head_proc = _git(root, "rev-parse", "HEAD")
    if head_proc.returncode == 127:  # git binary unavailable → cannot snapshot
        return RepoState(head=_NOT_A_REPO, index_digest="", tracked_count=None)
    head = head_proc.stdout.strip() if head_proc.returncode == 0 else "<unborn>"

    ls_proc = _git(root, "ls-files", "-s")
    if ls_proc.returncode == 0:
        digest = hashlib.sha256(ls_proc.stdout.encode()).hexdigest()
        count = len(ls_proc.stdout.splitlines())
    else:
        digest, count = "", None

    log_proc = _git(root, "reflog", "show", "HEAD", f"-n{_REFLOG_WINDOW}", "--format=%h %gs")
    reflog = tuple(log_proc.stdout.splitlines()) if log_proc.returncode == 0 else ()

    return RepoState(head=head, index_digest=digest, tracked_count=count, reflog=reflog)


def diff_repo(before: RepoState, after: RepoState) -> str | None:
    """Human-readable delta between two snapshots, or None if unchanged."""
    if before == after:
        return None
    parts: list[str] = []
    if before.head != after.head:
        parts.append(f"HEAD {before.head[:12]} -> {after.head[:12]}")
    if before.index_digest != after.index_digest:
        parts.append(f"index changed (tracked {before.tracked_count} -> {after.tracked_count})")
    return "; ".join(parts) if parts else None


def reflog_since(before: RepoState, after: RepoState) -> tuple[str, ...]:
    """HEAD reflog lines that landed between the two snapshots.

    Empty when the window cannot prove it — no baseline, or a baseline already
    scrolled past — so old operations are never presented as new ones.
    """
    if not before.reflog or before.reflog[0] not in after.reflog:
        return ()
    return after.reflog[: after.reflog.index(before.reflog[0])]


def _reading(entries: tuple[str, ...]) -> str:
    """The plainest reading the evidence supports — a reading, never a verdict."""
    if any(line.partition(" ")[2].startswith("merge ") for line in entries):
        return (
            "A merge of a local branch is what a parallel session landing its work in this "
            "shared checkout looks like: nothing is broken, but the tests ran against a "
            "moving tree — rerun them on a quiet checkout before trusting this result."
        )
    if entries:
        return (
            "None of them is a merge landing in this checkout. If nobody else was working "
            "here, a test wrote to the real repo — that is the bug to chase."
        )
    return (
        "Without reflog evidence both readings stay open: another session landing work in "
        "this shared checkout, or a test writing to the real repo."
    )


def describe_movement(before: RepoState, after: RepoState, root: Path) -> str | None:
    """Operator-facing report for a repo that moved under a test run, or None.

    HATS-1675: states the observation, quotes the reflog entries that landed in
    the window, and offers the reading they support — an equal explanation of a
    moved HEAD is a parallel session's merge, which the guard cannot rule out.
    """
    delta = diff_repo(before, after)
    if delta is None:
        return None

    entries = reflog_since(before, after)
    evidence = (
        ["reflog entries that appeared while the tests ran:", *(f"    {e}" for e in entries)]
        if entries
        else ["reflog: nothing attributable to this run (empty, expired or unreadable)."]
    )
    return "\n".join(
        [
            f"[repo-integrity] the repo at {root} moved while the tests ran: {delta}",
            *evidence,
            _reading(entries),
            "This guard observes the movement; it does not establish who caused it.",
        ]
    )
