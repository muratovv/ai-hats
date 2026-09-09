"""e2e (HATS-1501)

flow:   someone edits a library trait inside a linked worktree and asks for a
        read-only composition from that worktree, expecting their own edit to be
        the one that composes
cmds:
    git worktree add --detach <wt>
    python -m ai_hats config show-prompt --role role-curator
expect: stdout carries the trait as edited in the WORKTREE, not the main
        checkout's copy of it — asserted on content, never status, because the
        failure mode is exit 0 with the block present and carrying the wrong text
why:    the defect was silent, which is why it needs a real subprocess: in
        process `_detect_source_library_root(cwd)` already returned the
        worktree, so every in-process probe agreed with the fix while the shipped
        CLI still composed master. `AI_HATS_LIBRARY_ROOT` is deliberately unset
        here — setting it is the manual workaround this test exists to remove
"""
# comment-length: allow — the four-field catalog block, schema in gen_e2e_catalog.py

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import OverlayConfig, ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

from _helpers.env import checkout_pythonpath
from _helpers.git import git

# The curator prose folded from its own trait into the role (HATS-1900), so the
# worktree-only edit now lands in the role's injection.
ROLE_CONFIG = Path(
    "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/roles/role-curator/config.yaml"
)
ANCHOR = "  # ROLE: ROLE CURATOR\n"
SENTINEL = "SENTINEL_HATS_1501_WORKTREE_EDIT"
PROJECT_SENTINEL = "SENTINEL_HATS_1699_PROJECT_CONFIG"


def _bare_env(repo_root: Path) -> dict[str, str]:
    """Child env with the checkout importable but NO library-root override."""
    env = {**os.environ}
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)
    for leaked in ("AI_HATS_LIBRARY_ROOT", "AI_HATS_PROJECT_DIR", "AI_HATS_DIR"):
        env.pop(leaked, None)
    return env


@pytest.mark.integration
def test_worktree_library_edit_reaches_show_prompt(repo_root: Path, tmp_path: Path):
    # The worktree hangs off a CLONE, never off the developer's checkout: the
    # hop runs before the marker walk and accepts an onboarded main, so a
    # worktree of this repo composes the developer's own ai-hats.yaml. A clone
    # carries the tracked library and none of the gitignored project markers,
    # which is the un-onboarded main the hop is required to refuse.
    main = tmp_path / "main"
    git(tmp_path, "clone", "--local", str(repo_root), str(main))
    wt = tmp_path / "wt-1501"
    git(main, "worktree", "add", "--detach", str(wt))

    role_config = wt / ROLE_CONFIG
    original = role_config.read_text()
    assert ANCHOR in original, "role injection shape changed; update this test"
    role_config.write_text(original.replace(ANCHOR, f"{ANCHOR}\n  {SENTINEL}\n", 1))
    ProjectConfig(
        provider="claude",
        customizations={"role-curator": OverlayConfig(injection_append=PROJECT_SENTINEL)},
    ).save(wt / PROJECT_CONFIG)
    Assembler(wt).init()

    # Asserted directly, not inferred from the sentinels: which root resolved is
    # the thing that broke, and a sentinel can go missing for other reasons.
    root = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ai_hats.cli._entry import resolve_project; print(resolve_project().layout.root)",
        ],
        cwd=str(wt),
        env=_bare_env(wt),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert root.returncode == 0, root.stderr
    assert root.stdout.strip() == str(wt), f"resolved {root.stdout.strip()}, not the worktree"

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
    assert "ROLE CURATOR" in proc.stdout, "role injection did not compose at all"
    assert PROJECT_SENTINEL in proc.stdout, "worktree project config did not compose"
    assert SENTINEL in proc.stdout, (
        "composed the main checkout's library, not the worktree's — HATS-1501"
    )
