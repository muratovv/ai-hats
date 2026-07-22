"""E2E (HATS-1123): an uninitialised cwd must not replay migrations into the
project ``AI_HATS_DIR`` points at.

``dev_rule_e2e_gate`` artifact for the ``migrations.py`` / ``assembler.py``
guards. Reproduces the live incident: a sub-agent shell in a git worktree (no
``ai-hats.yaml`` — it is gitignored) carries an absolute ``AI_HATS_DIR`` aimed at
the MAIN checkout. ``migration_step`` defaults to 0 for the missing config, so
the whole registry replays; step 6's hooks-partition is the one pass keyed on
``ai_hats_dir`` rather than ``project_dir``, so it evicted MAIN's
skill-materialized hooks to ``user-hooks/`` while ``.claude/settings.json`` kept
pointing at the vacated path.

The discriminator is the EVICTION (a move into ``user-hooks/``), not the
hook's absence: a hook no longer backed by the composition is legitimately
swept as stale by ``materialize_runtime_hooks``, recoverably and on the record.
Verified fail-under-revert: with the guards reverted, ``user-hooks/`` receives
the sentinel.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_SENTINEL = "safety-guard-safety_gate.py"


def test_bare_cwd_does_not_evict_hooks_of_foreign_project(
    tmp_project, tmp_path: Path, repo_root: Path
) -> None:
    project = tmp_project.path

    hooks = project / ".agent" / "ai-hats" / "library" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / _SENTINEL).write_text("#!/usr/bin/env python3\nprint('sentinel')\n")
    (hooks / ".manifest").write_text("# ai-hats managed — do not edit\n" + _SENTINEL + "\n")

    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "_ai_hats_managed": "ai-hats:safety-guard:PreToolUse:Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "$CLAUDE_PROJECT_DIR/.agent/ai-hats/"
                                    "library/hooks/" + _SENTINEL,
                                }
                            ],
                        }
                    ]
                }
            },
            indent=2,
        )
        + "\n"
    )
    settings_before = settings.read_text()

    # The worktree shape: cwd with no ai-hats.yaml, pinned as the project, while
    # AI_HATS_DIR still points at the initialised checkout. The pair MATCHES, so
    # the HATS-897 pin guard accepts the override — this is what the launcher's
    # re-pin produced in the field.
    bare = tmp_path / "worktree-like"
    bare.mkdir()
    # Bind the subprocess to THIS checkout's source: sys.executable's editable
    # install resolves to the main checkout, which would exercise the merged
    # code rather than the change under test (and go green on a worktree that
    # never had the fix).
    src_paths = [repo_root / "src", *sorted((repo_root / "packages").glob("*/src"))]
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")},
        "PYTHONPATH": os.pathsep.join(str(p) for p in src_paths),
        "AI_HATS_DIR": str((project / ".agent" / "ai-hats").resolve()),
        "AI_HATS_PROJECT_DIR": str(bare.resolve()),
    }

    subprocess.run(  # noqa: S603 — fixed argv, dev-venv interpreter
        [sys.executable, "-m", "ai_hats._bump_internal"],
        cwd=str(bare), env=env, capture_output=True, text=True, timeout=300,
    )

    evicted = project / ".agent" / "ai-hats" / "user-hooks" / _SENTINEL
    assert not evicted.exists(), (
        f"{_SENTINEL} was evicted to user-hooks/ by a migration replay triggered "
        f"from an uninitialised cwd (HATS-1123)"
    )
    assert settings.read_text() == settings_before
