"""ADR-0014 Phase 1 (T15 / HATS-952) — standalone consumability for the session CLI.

Proves a third party can drive ``ai_hats_observe.cli.session`` — list / show /
list --tag — on a bare directory with the default worktree-free ``_seam``
resolvers (no ``ai-hats.yaml``, no composition, no integrator override), and that
importing the CLI pulls in no ``ai_hats`` integrator (the browse CLI is core-only).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from ai_hats_observe.artifacts import METRICS_JSON, session_dirname
from ai_hats_observe.cli import _seam
from ai_hats_observe.cli.session import session

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _pin_wt_free_seam(monkeypatch) -> None:
    """Reset the seam to its wt-free defaults for the test.

    The integrator override mutates the shared ``_seam`` module for the whole
    process, so an earlier test that imported ``ai_hats.cli`` would otherwise
    leave the integrator resolvers in place. Pin the standalone defaults here.
    """
    monkeypatch.setattr(_seam, "_PROJECT_DIR", _seam._default_project_dir)
    monkeypatch.setattr(_seam, "_RUNS_DIR", _seam._default_runs_dir)
    monkeypatch.setattr(_seam, "_TAG_FILTER_PARSER", _seam._default_tag_filter_parser)


def _make_session(runs: Path, sid: str, *, metrics: dict) -> None:
    sdir = runs / session_dirname(sid)
    sdir.mkdir(parents=True)
    (sdir / METRICS_JSON).write_text(json.dumps(metrics))


def test_standalone_session_browse_wt_free(tmp_path: Path, monkeypatch) -> None:
    """list / show / list --tag on a bare dir with the wt-free seam — no
    ``ai-hats.yaml``, no integrator override. The runs live under the default
    ``.agent/sessions/runs`` layout the wt-free ``_RUNS_DIR`` injects."""
    assert not (tmp_path / "ai-hats.yaml").exists()
    runs = tmp_path / ".agent" / "sessions" / "runs"
    _make_session(runs, "20260401T100000Z_a1", metrics={
        "role": "primary", "provider": "claude", "turns": 5,
        "tool_calls": 9, "tokens": {"output": 1234}, "tags": {"env": "prod"},
    })
    _make_session(runs, "20260402T100000Z_b2", metrics={
        "role": "reviewer", "provider": "claude", "turns": 2,
        "tool_calls": 3, "tokens": {"output": 42}, "tags": {"env": "dev"},
    })
    monkeypatch.chdir(tmp_path)
    _pin_wt_free_seam(monkeypatch)
    runner = CliRunner()

    # list --json --all — both sessions, resolved via the wt-free default layout
    listed = runner.invoke(session, ["list", "--json", "--all"])
    assert listed.exit_code == 0, listed.output
    ids = sorted(d["session_id"] for d in json.loads(listed.output))
    assert ids == ["20260401T100000Z_a1", "20260402T100000Z_b2"], listed.output

    # show — per-session metrics (line-rendered, not a table)
    shown = runner.invoke(session, ["show", "20260401T100000Z_a1"])
    assert shown.exit_code == 0, shown.output
    assert "primary" in shown.output

    # list --tag — the seam-default k=v parser filters to the matching session
    filtered = runner.invoke(session, ["list", "--json", "--all", "--tag", "env=prod"])
    assert filtered.exit_code == 0, filtered.output
    filtered_ids = [d["session_id"] for d in json.loads(filtered.output)]
    assert filtered_ids == ["20260401T100000Z_a1"], filtered.output


def test_session_cli_import_pulls_no_integrator() -> None:
    """RED-under-revert: ``import ai_hats_observe.cli.session`` must pull no
    ``ai_hats`` integrator. Runs in a clean subprocess (fresh ``sys.modules``)
    with observe + core on ``PYTHONPATH`` — so a hard ``ai_hats.*`` import (e.g.
    a re-inlined ``from ..paths import runs_dir``) would land in ``sys.modules``
    and fail this.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(_WORKSPACE_ROOT / "packages" / "ai-hats-observe" / "src"),
            str(_WORKSPACE_ROOT / "packages" / "ai-hats-core" / "src"),
            env.get("PYTHONPATH", ""),
        ]
    )
    code = (
        "import sys, ai_hats_observe.cli.session as s\n"
        "assert 'cli' in s.__file__, s.__file__\n"
        "assert 'ai_hats' not in sys.modules, "
        "'importing ai_hats_observe.cli.session pulled the ai_hats integrator'\n"
    )
    result = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
