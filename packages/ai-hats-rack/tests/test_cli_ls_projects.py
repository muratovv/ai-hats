"""HATS-1081: cross-project sweep — `rack ls --root <path>` / `--projects` scan
backlogs across several projects, each row marked by its project (root_id).

Thin CLI layer over `Workspace.discover(roots)` (already multi-root). The default
(no `--root`/`--projects`) stays the current project only; a read never locks.
"""

from __future__ import annotations

import json
import shutil

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.definition import packaged_definition_source


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


def _make_project_with_hyp(base, name):
    proj = base / name
    tracker = proj / ".agent" / "ai-hats" / "tracker"
    (tracker / "backlog" / "tasks").mkdir(parents=True)
    hyp = tracker / "hypotheses"
    hyp.mkdir(parents=True)
    (hyp / "backlog.yaml").write_text(packaged_definition_source("hypotheses"), encoding="utf-8")
    return proj, hyp


def test_backlog_filter_spans_projects(runner, tmp_path, monkeypatch):
    # the headline: --backlog hyp --projects all hits the hyp backlog in EVERY project
    proj_a, hyp_a = _make_project_with_hyp(tmp_path, "projA")
    proj_b, hyp_b = _make_project_with_hyp(tmp_path, "projB")
    _seed_card(hyp_a, "HYP-1", "idea A")
    _seed_card(hyp_b, "HYP-1", "idea B")
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))
    runner.invoke(main, ["root", "add", str(proj_b)], catch_exceptions=False)

    monkeypatch.chdir(proj_a)
    out = runner.invoke(
        main, ["ls", "--backlog", "hyp", "--projects", "all", "--json"], catch_exceptions=False
    )

    assert out.exit_code == 0, out.output
    rows = json.loads(out.output)["tasks"]
    assert {r["project"] for r in rows} == {"projA", "projB"}
    assert all(r["backlog"] == "hyp" and r["id"].startswith("HYP-") for r in rows)


def test_projects_all_skips_unreachable_root(runner, tmp_path, monkeypatch):
    # C3: a registered root that vanished is skipped, non-silently — sweep survives (R5).
    proj_a, a_tasks = _make_project(tmp_path, "projA")
    proj_b, b_tasks = _make_project(tmp_path, "projB")
    _seed_card(a_tasks, "HATS-1", "A")
    _seed_card(b_tasks, "HATS-1", "B")
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))
    runner.invoke(main, ["root", "add", str(proj_b)], catch_exceptions=False)
    shutil.rmtree(proj_b)

    monkeypatch.chdir(proj_a)
    out = runner.invoke(main, ["ls", "--projects", "all", "--json"], catch_exceptions=False)

    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert {r["project"] for r in data["tasks"]} == {"projA"}
    assert data["skipped"] == ["projB"]


def test_root_to_non_project_fails_fast(runner, tmp_path, monkeypatch):
    # C6: an explicit --root to a non-project is a hard error (not a silent skip).
    proj_a, a_tasks = _make_project(tmp_path, "projA")
    _seed_card(a_tasks, "HATS-1", "A")
    not_proj = tmp_path / "notproj"
    not_proj.mkdir()

    monkeypatch.chdir(proj_a)
    out = runner.invoke(main, ["ls", "--root", str(not_proj), "--json"], catch_exceptions=False)

    assert out.exit_code == 1, out.output


def test_projects_all_empty_registry_shows_cwd(runner, tmp_path, monkeypatch):
    # C5: --projects all with an empty registry is just the CWD project, not a crash.
    proj_a, a_tasks = _make_project(tmp_path, "projA")
    _seed_card(a_tasks, "HATS-1", "A")
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))

    monkeypatch.chdir(proj_a)
    out = runner.invoke(main, ["ls", "--projects", "all", "--json"], catch_exceptions=False)

    assert out.exit_code == 0, out.output
    assert {r["project"] for r in json.loads(out.output)["tasks"]} == {"projA"}


def test_context_routes_cross_project_by_qualifier(runner, tmp_path, monkeypatch):
    # C11 / step 6: `context <root>:<id>` routes a READ to a registered project.
    proj_a, a_tasks = _make_project(tmp_path, "projA")
    proj_b, b_tasks = _make_project(tmp_path, "projB")
    _seed_card(a_tasks, "HATS-1", "A card")
    _seed_card(b_tasks, "HATS-9", "B card")
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))
    runner.invoke(main, ["root", "add", str(proj_b)], catch_exceptions=False)

    monkeypatch.chdir(proj_a)
    out = runner.invoke(main, ["context", "projB:HATS-9", "--json"], catch_exceptions=False)

    assert out.exit_code == 0, out.output
    assert "B card" in out.output


def test_context_cross_project_by_explicit_root(runner, tmp_path, monkeypatch):
    # --root mounts an unregistered project so a qualified id resolves there.
    proj_a, a_tasks = _make_project(tmp_path, "projA")
    proj_b, b_tasks = _make_project(tmp_path, "projB")
    _seed_card(a_tasks, "HATS-1", "A card")
    _seed_card(b_tasks, "HATS-9", "B card")

    monkeypatch.chdir(proj_a)
    out = runner.invoke(
        main,
        ["context", "projB:HATS-9", "--root", str(proj_b), "--json"],
        catch_exceptions=False,
    )

    assert out.exit_code == 0, out.output
    assert "B card" in out.output
