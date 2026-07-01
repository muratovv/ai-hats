"""HATS-887 — pure snapshot/diff of a repo's test-mutable surface, for the
session-scoped real-repo integrity tripwire.

Watches the checked-out branch's HEAD + THIS worktree's index (``git ls-files
-s`` digest), NOT all refs: in a multi-agent shared clone other agents
legitimately move sibling branches / master, which an all-refs snapshot would
misreport as a test mutation. HEAD + index catch the incident class (a commit
onto the checkout, or the staged −178393 tree delete) while staying immune to
that concurrent external ref churn. Split out pure for unit-testability.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_NOT_A_REPO = "<not-a-git-repo>"


@dataclass(frozen=True)
class RepoState:
    """Value snapshot of a repo's test-mutable surface. Compares by value."""

    head: str
    index_digest: str  # sha256 of `git ls-files -s`; "" when not a repo
    tracked_count: int | None  # kept for a human-readable delta message

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

    return RepoState(head=head, index_digest=digest, tracked_count=count)


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
