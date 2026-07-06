"""ADR-0014 Phase 2 (T16b / HATS-934) — standalone consumability for the CLI.

Proves a third party can drive ``ai_hats_tracker.cli.task`` — create / log / list
/ show / transition — on a bare directory with the default worktree-free ``_seam``
factory (no wt, no ``ai-hats.yaml``, no integrator), and that importing the CLI
pulls in no ``ai_hats_wt`` (the wt coupling is a soft optional extra).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from ai_hats_tracker.cli import _seam
from ai_hats_tracker.cli.task import task

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _pin_wt_free_seam(monkeypatch) -> None:
    """Reset the seam to its wt-free defaults for the test.

    The integrator override mutates the shared ``_seam`` module for the whole
    process, so an earlier test that imported ``ai_hats.cli`` would otherwise
    leave the wt-wired factory in place. Pin the standalone defaults here.
    """
    monkeypatch.setattr(_seam, "_MANAGER_FACTORY", _seam._default_task_manager)
    monkeypatch.setattr(_seam, "_PROJECT_DIR", _seam._default_project_dir)
    monkeypatch.setattr(_seam, "_GUARD_LINKED_WT", _seam._default_guard_not_inside_linked_worktree)
    monkeypatch.setattr(_seam, "_WORKTREES_DIR", None)


def test_cli_task_import_pulls_no_ai_hats_wt():
    """RED-under-revert: ``import ai_hats_tracker.cli.task`` must pull no
    ``ai_hats_wt``. Runs in a clean subprocess (fresh ``sys.modules``) with
    tracker + core on ``PYTHONPATH`` and wt importable from site-packages — so a
    module-level (hard) wt import would land in ``sys.modules`` and fail this.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(_WORKSPACE_ROOT / "packages" / "ai-hats-tracker" / "src"),
            str(_WORKSPACE_ROOT / "packages" / "ai-hats-core" / "src"),
            env.get("PYTHONPATH", ""),
        ]
    )
    code = (
        "import sys, ai_hats_tracker.cli.task as t\n"
        "assert 'cli' in t.__file__, t.__file__\n"
        "assert 'ai_hats_wt' not in sys.modules, "
        "'importing ai_hats_tracker.cli.task pulled ai_hats_wt (hard import?)'\n"
    )
    result = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_standalone_backlog_cli_drives_fsm(tmp_path: Path, monkeypatch) -> None:
    """create → log → list → show → transition on a bare dir with the default
    wt-free factory — no ``ai-hats.yaml``, no wt, no integrator override."""
    assert not (tmp_path / "ai-hats.yaml").exists()
    (tmp_path / ".agent").mkdir()
    monkeypatch.chdir(tmp_path)
    _pin_wt_free_seam(monkeypatch)
    runner = CliRunner()

    created = runner.invoke(task, ["create", "Standalone probe"])
    assert created.exit_code == 0, created.output
    match = re.search(r"(HATS-\d+)", created.output)
    assert match, created.output
    task_id = match.group(1)

    logged = runner.invoke(task, ["log", task_id, "probing the standalone CLI"])
    assert logged.exit_code == 0, logged.output

    listed = runner.invoke(task, ["list"])
    assert listed.exit_code == 0, listed.output
    assert task_id in listed.output

    shown = runner.invoke(task, ["show", task_id])
    assert shown.exit_code == 0, shown.output
    assert "Standalone probe" in shown.output

    moved = runner.invoke(task, ["transition", task_id, "plan"])
    assert moved.exit_code == 0, moved.output

    # The card landed on disk under the wt-free `.agent` layout the default
    # factory injects — proof the whole flow ran without an integrator.
    assert (tmp_path / ".agent" / "tasks" / task_id / "task.yaml").exists()
