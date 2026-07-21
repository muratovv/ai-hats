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


def test_projects_all_spans_registry_union_cwd(runner, tmp_path, monkeypatch):
    proj_a, a_tasks = _make_project(tmp_path, "projA")
    proj_b, b_tasks = _make_project(tmp_path, "projB")
    _seed_card(a_tasks, "HATS-1", "task in A")
    _seed_card(b_tasks, "HATS-1", "task in B")
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))
    runner.invoke(main, ["root", "add", str(proj_b)], catch_exceptions=False)

    # standing in A, --projects all = registry (B) ∪ CWD (A)
    monkeypatch.chdir(proj_a)
    out = runner.invoke(main, ["ls", "--projects", "all", "--json"], catch_exceptions=False)

    assert out.exit_code == 0, out.output
    assert {r["project"] for r in json.loads(out.output)["tasks"]} == {"projA", "projB"}


def test_projects_by_name_selects_subset(runner, tmp_path, monkeypatch):
    proj_a, a_tasks = _make_project(tmp_path, "projA")
    proj_b, b_tasks = _make_project(tmp_path, "projB")
    proj_c, c_tasks = _make_project(tmp_path, "projC")
    for t, tag in ((a_tasks, "A"), (b_tasks, "B"), (c_tasks, "C")):
        _seed_card(t, "HATS-1", f"task {tag}")
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))
    runner.invoke(main, ["root", "add", str(proj_b)], catch_exceptions=False)
    runner.invoke(main, ["root", "add", str(proj_c)], catch_exceptions=False)

    monkeypatch.chdir(proj_a)
    out = runner.invoke(main, ["ls", "--projects", "projB", "--json"], catch_exceptions=False)

    # named subset ∪ CWD: projB (named) + projA (CWD), not projC
    assert {r["project"] for r in json.loads(out.output)["tasks"]} == {"projA", "projB"}
