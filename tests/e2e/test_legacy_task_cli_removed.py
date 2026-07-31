"""HATS-1260 e2e gate — the legacy ``ai-hats task`` CLI is unmounted.

At the real subprocess boundary (module form per HATS-790 — no console
script): the wired ``main`` has no ``task`` group and ``--help`` does not
advertise one. No exit-code probe on ``task --help``: per HATS-087/1202 an
unknown first token is a bare positional prompt (supervisor ruling
2026-07-28 — no tombstone, R2 "no shim" holds), so it wrap-launches.
Fail-under-revert: restore ``main.add_command(task.task)`` → both go red.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

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
