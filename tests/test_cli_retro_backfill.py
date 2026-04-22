"""Tests for ai-hats retro --backfill (HATS-160)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.retro.backfill import (
    Candidate,
    backfill_one,
    find_candidates,
    run_backfill,
)
from ai_hats.retro.builder import BuilderMode


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_session(project: Path, sid: str, *, role="assistant", turns=5, tool_calls=10):
    d = project / ".gitlog" / f"session_{sid}"
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({
        "role": role, "turns": turns, "tool_calls": tool_calls, "exit_code": 0,
    }))
    # Minimal audit for builder to have something to parse if it ever runs.
    (d / "audit.md").write_text(f"# Session Audit: {sid}\n\n## Turn 1\nok\n")
    return d


def _make_existing_retro(project: Path, sid: str, mode: str = "programmatic"):
    d = project / ".agent" / "retrospectives" / "sessions" / mode
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.md").write_text(f"existing retro for {sid}\n")


# ------------------------------------------------------------------
# find_candidates — filter logic
# ------------------------------------------------------------------


class TestFindCandidates:
    def test_empty_project(self, tmp_path):
        cs, skipped = find_candidates(tmp_path)
        assert cs == [] and skipped == []

    def test_fresh_session_is_candidate(self, tmp_path):
        _make_session(tmp_path, "20260422-100000-1", turns=5, tool_calls=10)
        cs, _ = find_candidates(tmp_path)
        assert len(cs) == 1
        assert cs[0].session_id == "20260422-100000-1"
        assert cs[0].turns == 5 and cs[0].tool_calls == 10

    def test_existing_retro_is_skipped(self, tmp_path):
        _make_session(tmp_path, "SID1")
        _make_existing_retro(tmp_path, "SID1")
        cs, skipped = find_candidates(tmp_path)
        assert cs == []
        assert any("already exists" in s.reason for s in skipped)

    def test_force_overrides_existing(self, tmp_path):
        _make_session(tmp_path, "SID1")
        _make_existing_retro(tmp_path, "SID1")
        cs, _ = find_candidates(tmp_path, force=True)
        assert len(cs) == 1

    def test_role_judge_skipped(self, tmp_path):
        _make_session(tmp_path, "SID1", role="judge")
        cs, skipped = find_candidates(tmp_path)
        assert cs == []
        assert any("role=judge" in s.reason for s in skipped)

    def test_role_test_agent_skipped(self, tmp_path):
        _make_session(tmp_path, "SID1", role="test-agent")
        cs, _ = find_candidates(tmp_path)
        assert cs == []

    def test_missing_metrics_skipped(self, tmp_path):
        d = tmp_path / ".gitlog" / "session_SID1"
        d.mkdir(parents=True)
        cs, skipped = find_candidates(tmp_path)
        assert cs == []
        assert any("metrics.json missing" in s.reason for s in skipped)

    def test_unreadable_metrics_skipped(self, tmp_path):
        d = tmp_path / ".gitlog" / "session_SID1"
        d.mkdir(parents=True)
        (d / "metrics.json").write_text("not json{{{")
        cs, skipped = find_candidates(tmp_path)
        assert cs == []
        assert any("unreadable" in s.reason for s in skipped)

    def test_min_turns_filter(self, tmp_path):
        _make_session(tmp_path, "SID_small", turns=0, tool_calls=0)
        _make_session(tmp_path, "SID_big", turns=5, tool_calls=10)
        cs, _ = find_candidates(tmp_path, min_turns=1)
        assert [c.session_id for c in cs] == ["SID_big"]

    def test_tool_calls_alone_is_enough(self, tmp_path):
        # 0 turns but 5 tool_calls → still keep (builder might have useful data).
        _make_session(tmp_path, "SID_tools", turns=0, tool_calls=5)
        cs, _ = find_candidates(tmp_path, min_turns=1)
        assert len(cs) == 1

    def test_since_filter(self, tmp_path):
        _make_session(tmp_path, "20260401-100000-1")
        _make_session(tmp_path, "20260422-100000-1")
        cs, _ = find_candidates(tmp_path, since="2026-04-10")
        assert [c.session_id for c in cs] == ["20260422-100000-1"]

    def test_only_filter(self, tmp_path):
        _make_session(tmp_path, "SID1")
        _make_session(tmp_path, "SID2")
        _make_session(tmp_path, "SID3")
        cs, _ = find_candidates(tmp_path, only=["SID1", "SID3"])
        assert [c.session_id for c in cs] == ["SID1", "SID3"]

    def test_candidates_sorted_chronologically(self, tmp_path):
        _make_session(tmp_path, "20260422-100000-1")
        _make_session(tmp_path, "20260401-100000-1")
        _make_session(tmp_path, "20260415-100000-1")
        cs, _ = find_candidates(tmp_path)
        assert [c.session_id for c in cs] == [
            "20260401-100000-1", "20260415-100000-1", "20260422-100000-1",
        ]


# ------------------------------------------------------------------
# backfill_one — per-session execution
# ------------------------------------------------------------------


def _stub_builder_class(saved_path: Path):
    class _Stub:
        def __init__(self, *a, **kw): pass
        def build_and_save(self, sid, mode=None):
            saved_path.write_text(f"stub retro {sid}\n")
            return saved_path
    return _Stub


def test_backfill_one_saved(tmp_path, monkeypatch):
    sd = _make_session(tmp_path, "SID1")
    out = tmp_path / "out.md"
    monkeypatch.setattr("ai_hats.retro.backfill.SessionRetroBuilder", _stub_builder_class(out))

    res = backfill_one(
        tmp_path, Candidate("SID1", sd, 5, 10, "assistant"),
        mode=BuilderMode.PROGRAMMATIC, timeout=60, dry_run=False,
    )
    assert res.status == "saved"
    assert res.detail == str(out)
    log = (sd / "retro.log").read_text()
    assert "backfill\tstart" in log
    assert "backfill\tsaved" in log


def test_backfill_one_failed_never_raises(tmp_path, monkeypatch):
    sd = _make_session(tmp_path, "SID1")

    class _Boom:
        def __init__(self, *a, **kw): pass
        def build_and_save(self, sid, mode=None):
            raise RuntimeError("kaboom")

    monkeypatch.setattr("ai_hats.retro.backfill.SessionRetroBuilder", _Boom)

    res = backfill_one(
        tmp_path, Candidate("SID1", sd, 5, 10, "assistant"),
        mode=BuilderMode.PROGRAMMATIC, timeout=60, dry_run=False,
    )
    assert res.status == "failed"
    assert "kaboom" in res.detail
    log = (sd / "retro.log").read_text()
    assert "backfill\tfailed" in log


def test_backfill_one_dry_run_skips_builder(tmp_path, monkeypatch):
    sd = _make_session(tmp_path, "SID1")

    class _ShouldNotRun:
        def __init__(self, *a, **kw): raise AssertionError("builder must not be called")

    monkeypatch.setattr("ai_hats.retro.backfill.SessionRetroBuilder", _ShouldNotRun)

    res = backfill_one(
        tmp_path, Candidate("SID1", sd, 5, 10, "assistant"),
        mode=BuilderMode.PROGRAMMATIC, timeout=60, dry_run=True,
    )
    assert res.status == "dry_run"
    assert "backfill\tdry_run" in (sd / "retro.log").read_text()


# ------------------------------------------------------------------
# run_backfill — orchestrator
# ------------------------------------------------------------------


def test_run_backfill_summary(tmp_path, monkeypatch):
    _make_session(tmp_path, "SID1")
    _make_session(tmp_path, "SID2")
    out = tmp_path / "retro.md"
    monkeypatch.setattr(
        "ai_hats.retro.backfill.SessionRetroBuilder", _stub_builder_class(out),
    )
    printed: list[str] = []
    summary = run_backfill(tmp_path, printer=printed.append)
    assert summary.total_candidates == 2
    assert summary.saved == 2
    assert summary.failed == 0
    assert len(printed) == 2
    assert all("saved" in line for line in printed)


def test_run_backfill_mixed_success_and_failure(tmp_path, monkeypatch):
    _make_session(tmp_path, "SID_ok")
    _make_session(tmp_path, "SID_fail")

    class _SelectiveBuilder:
        def __init__(self, *a, **kw): pass
        def build_and_save(self, sid, mode=None):
            if "fail" in sid:
                raise RuntimeError("builder error")
            out = tmp_path / f"{sid}.md"
            out.write_text("ok")
            return out

    monkeypatch.setattr("ai_hats.retro.backfill.SessionRetroBuilder", _SelectiveBuilder)
    summary = run_backfill(tmp_path, printer=lambda _: None)
    assert summary.saved == 1
    assert summary.failed == 1


# ------------------------------------------------------------------
# CLI smoke
# ------------------------------------------------------------------


@pytest.fixture
def cli_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    runner = CliRunner()
    # ai-hats.yaml is required for _project_dir; minimal valid config.
    (project / "ai-hats.yaml").write_text(
        "schema_version: 2\nprovider: claude\nactive_role: assistant\n"
    )
    return project, runner


def test_cli_backfill_and_session_id_mutually_exclusive(cli_project):
    project, runner = cli_project
    r = runner.invoke(main, ["retro", "--backfill", "SID_XXX"])
    assert r.exit_code == 2
    assert "mutually exclusive" in r.output


def test_cli_backfill_no_candidates(cli_project):
    project, runner = cli_project
    r = runner.invoke(main, ["retro", "--backfill"])
    assert r.exit_code == 0
    assert "No candidates" in r.output


def test_cli_backfill_dry_run_lists_candidates(cli_project, monkeypatch):
    project, runner = cli_project
    _make_session(project, "SID1", turns=5, tool_calls=10)

    class _ShouldNotRun:
        def __init__(self, *a, **kw): raise AssertionError("builder ran in --dry-run")

    monkeypatch.setattr("ai_hats.retro.backfill.SessionRetroBuilder", _ShouldNotRun)

    r = runner.invoke(main, ["retro", "--backfill", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "SID1" in r.output
    assert "dry-run" in r.output
    assert "saved=0" in r.output
    assert "dry_run=1" in r.output


def test_cli_backfill_partial_failure_exits_1(cli_project, monkeypatch):
    project, runner = cli_project
    _make_session(project, "SID_ok")
    _make_session(project, "SID_fail")

    class _SelectiveBuilder:
        def __init__(self, *a, **kw): pass
        def build_and_save(self, sid, mode=None):
            if "fail" in sid:
                raise RuntimeError("bad")
            out = project / f"{sid}.md"
            out.write_text("ok")
            return out

    monkeypatch.setattr("ai_hats.retro.backfill.SessionRetroBuilder", _SelectiveBuilder)

    r = runner.invoke(main, ["retro", "--backfill"])
    assert r.exit_code == 1
    assert "saved=1" in r.output
    assert "failed=1" in r.output


def test_cli_backfill_only_filter(cli_project, monkeypatch):
    project, runner = cli_project
    _make_session(project, "SID_keep")
    _make_session(project, "SID_drop")
    called: list[str] = []

    class _Recorder:
        def __init__(self, *a, **kw): pass
        def build_and_save(self, sid, mode=None):
            called.append(sid)
            out = project / f"{sid}.md"
            out.write_text("ok")
            return out

    monkeypatch.setattr("ai_hats.retro.backfill.SessionRetroBuilder", _Recorder)

    r = runner.invoke(main, ["retro", "--backfill", "--only", "SID_keep"])
    assert r.exit_code == 0, r.output
    assert called == ["SID_keep"]
