"""HATS-1081: the cross-project roots registry — `rack root add/ls/rm` over
`~/.ai-hats/roots.yaml` (path overridable via RACK_ROOTS_FILE for tests)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _make_project(base, name):
    proj = base / name
    (proj / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks").mkdir(parents=True)
    return proj


def test_root_add_ls_rm_roundtrip(runner, tmp_path, monkeypatch):
    proj_a = _make_project(tmp_path, "projA")
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))

    added = runner.invoke(main, ["root", "add", str(proj_a), "--json"], catch_exceptions=False)
    assert added.exit_code == 0, added.output

    ls = runner.invoke(main, ["root", "ls", "--json"], catch_exceptions=False)
    assert [r["root_id"] for r in json.loads(ls.output)["roots"]] == ["projA"]

    rm = runner.invoke(main, ["root", "rm", str(proj_a), "--json"], catch_exceptions=False)
    assert json.loads(rm.output)["removed"] is True

    ls2 = runner.invoke(main, ["root", "ls", "--json"], catch_exceptions=False)
    assert json.loads(ls2.output)["roots"] == []


def test_root_add_dedupes(runner, tmp_path, monkeypatch):
    proj_a = _make_project(tmp_path, "projA")
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))
    runner.invoke(main, ["root", "add", str(proj_a)], catch_exceptions=False)
    runner.invoke(main, ["root", "add", str(proj_a)], catch_exceptions=False)
    ls = runner.invoke(main, ["root", "ls", "--json"], catch_exceptions=False)
    assert len(json.loads(ls.output)["roots"]) == 1


def test_root_add_rejects_non_project(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("RACK_ROOTS_FILE", str(tmp_path / "reg.yaml"))
    not_proj = tmp_path / "empty"
    not_proj.mkdir()
    out = runner.invoke(main, ["root", "add", str(not_proj), "--json"], catch_exceptions=False)
    assert out.exit_code == 1, out.output
