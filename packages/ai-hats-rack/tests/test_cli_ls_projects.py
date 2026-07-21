"""HATS-1081: cross-project sweep — `rack ls --root <path>` / `--projects` scan
backlogs across several projects, each row marked by its project (root_id).

Thin CLI layer over `Workspace.discover(roots)` (already multi-root). The default
(no `--root`/`--projects`) stays the current project only; a read never locks.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _make_project(base, name):
    """A minimal project: `.agent/` marker (walk-up target) + tasks catalog."""
    proj = base / name
    tasks = proj / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks.mkdir(parents=True)
    return proj, tasks


def _seed_card(tasks_dir, card_id, title):
    d = tasks_dir / card_id
    d.mkdir(parents=True)
    (d / "task.yaml").write_text(
        f"id: {card_id}\ntitle: {title}\nstate: brainstorm\n", encoding="utf-8"
    )


def test_ls_root_spans_projects_with_marker(runner, tmp_path, monkeypatch):
    proj_a, a_tasks = _make_project(tmp_path, "projA")
    proj_b, b_tasks = _make_project(tmp_path, "projB")
    _seed_card(a_tasks, "HATS-1", "task in A")
    _seed_card(b_tasks, "HATS-1", "task in B")

    # standing in A, sweep A (CWD) ∪ B (explicit --root)
    monkeypatch.chdir(proj_a)
    result = runner.invoke(main, ["ls", "--root", str(proj_b), "--json"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)["tasks"]
    assert {r["project"] for r in rows} == {"projA", "projB"}
