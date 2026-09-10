"""e2e (HATS-1260)

flow:   a developer invoking legacy task CLI commands
cmds:
    ai-hats task list  # no-resolve: pins that this CLI was removed
expect: CLI exits with error code explaining legacy task CLI is replaced by rack command
why: without legacy CLI removal guards, deprecated task subcommands execute stale task
     logic"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_ai_hats(cwd: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats <args>`` against the current checkout."""
    env = os.environ.copy()
    from _helpers.env import checkout_pythonpath

    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def test_task_group_is_unmounted(tmp_path: Path) -> None:
    """The wired CLI registers no ``task`` group (real interpreter, real wiring)."""
    env = os.environ.copy()
    from _helpers.env import checkout_pythonpath

    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ai_hats.cli import main; raise SystemExit(1 if 'task' in main.commands else 0)",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        timeout=30.0,
    )
    assert r.returncode == 0, f"legacy `task` group still mounted on main\nSTDERR:\n{r.stderr}"


def test_top_level_help_lists_no_task_group(tmp_path: Path) -> None:
    """``ai-hats --help`` succeeds and its command roster has no ``task`` row."""
    r = _run_ai_hats(tmp_path, "--help")
    assert r.returncode == 0, f"--help must stay healthy\nSTDERR:\n{r.stderr}"
    assert not re.search(r"^\s+task\b", r.stdout, re.MULTILINE), (
        f"`task` still advertised in --help:\n{r.stdout}"
    )
