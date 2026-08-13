"""HATS-1501: a library edit in a linked worktree must reach read-only composition.

The defect this pins was silent, which is why it needs a real subprocess: in
process ``_detect_source_library_root(cwd)`` already returned the worktree, so
every in-process probe agreed with the fix while the shipped CLI still composed
master. Only a real ``git worktree`` + a real interpreter run with a real cwd
reproduces it — and the failure mode is exit 0 with the edited block present and
carrying the WRONG text, so the assertion has to be on content, never status.

``AI_HATS_LIBRARY_ROOT`` is deliberately unset here: setting it is exactly the
manual workaround whose necessity this test exists to remove.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath
from _helpers.git import git

TRAIT = Path("packages/ai-hats-library/src/ai_hats_library/usage/traits/library-curator/config.yaml")
SENTINEL = "SENTINEL_HATS_1501_WORKTREE_EDIT"


def _bare_env(repo_root: Path) -> dict[str, str]:
    """Child env with the checkout importable but NO library-root override."""
    env = {**os.environ}
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)
    for leaked in ("AI_HATS_LIBRARY_ROOT", "AI_HATS_PROJECT_DIR", "AI_HATS_DIR"):
        env.pop(leaked, None)
    return env


@pytest.mark.integration
def test_worktree_library_edit_reaches_show_prompt(repo_root: Path, tmp_path: Path):
    wt = tmp_path / "wt-1501"
    git(repo_root, "worktree", "add", "--detach", str(wt))
    try:
        trait = wt / TRAIT
        original = trait.read_text()
        assert "## LIBRARY CURATOR" in original, "trait shape changed; update this test"
        trait.write_text(
            original.replace("  ## LIBRARY CURATOR\n", f"  ## LIBRARY CURATOR\n\n  {SENTINEL}\n", 1)
        )

        proc = subprocess.run(
            [sys.executable, "-m", "ai_hats", "config", "show-prompt", "--role", "role-curator"],
            cwd=str(wt),
            env=_bare_env(wt),
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert proc.returncode == 0, proc.stderr
        # Positive control first: if the trait vanished entirely, a missing
        # sentinel would prove nothing about WHICH checkout was composed.
        assert "LIBRARY CURATOR" in proc.stdout, "trait did not compose at all"
        assert SENTINEL in proc.stdout, (
            "composed the main checkout's library, not the worktree's — HATS-1501"
        )
    finally:
        git(repo_root, "worktree", "remove", "--force", str(wt))
