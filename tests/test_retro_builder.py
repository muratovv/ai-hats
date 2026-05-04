"""Tests for SessionRetroBuilder — programmatic, llm modes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_hats.retro.builder import BuilderMode, SessionRetroBuilder
from ai_hats.retro.loader import load
from ai_hats.retro.session_retro import SessionRetroV1

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "real_session"
FIXTURE_SESSION_ID = "20260406-034154-1"


# --- helpers ---


def _make_project_with_session(
    project: Path,
    session_id: str,
    *,
    audit_text: str | None = None,
    metrics: dict | None = None,
) -> Path:
    """Create a fake project with one session under .gitlog/."""
    sdir = project / ".gitlog" / f"session_{session_id}"
    sdir.mkdir(parents=True, exist_ok=True)
    if audit_text is not None:
        (sdir / "audit.md").write_text(audit_text)
    if metrics is not None:
        (sdir / "metrics.json").write_text(json.dumps(metrics))
    return sdir


def _make_project_with_real_fixture(project: Path) -> Path:
    sdir = project / ".gitlog" / f"session_{FIXTURE_SESSION_ID}"
    sdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_DIR / "audit.md", sdir / "audit.md")
    shutil.copy(FIXTURE_DIR / "metrics.json", sdir / "metrics.json")
    return sdir


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return tmp_path


# --- programmatic mode ---


def test_programmatic_builds_valid_session_retro_v1(project: Path) -> None:
    _make_project_with_real_fixture(project)
    builder = SessionRetroBuilder(project)
    retro = builder.build(FIXTURE_SESSION_ID, mode=BuilderMode.PROGRAMMATIC)
    assert isinstance(retro, SessionRetroV1)
    assert retro.session_id == FIXTURE_SESSION_ID
    assert retro.role == "assistant"
    assert retro.metrics.turns == 6
    assert retro.metrics.tool_calls == 15
    assert retro.metrics.tokens_in == 1200
    assert retro.observations == []
    assert "Session of 6 turn(s)" in retro.summary


def test_programmatic_handles_missing_metrics_json(project: Path) -> None:
    _make_project_with_session(
        project,
        "20260408-101010-1",
        audit_text="# Session Audit: 20260408-101010-1\n- **Role**: go-dev\n",
    )
    builder = SessionRetroBuilder(project)
    retro = builder.build("20260408-101010-1")
    assert retro.metrics.exit_code == 0
    assert retro.metrics.turns == 0
    assert retro.metrics.tool_calls == 0
    assert retro.role == "go-dev"  # parsed from audit header


def test_programmatic_strips_session_prefix(project: Path) -> None:
    _make_project_with_session(
        project,
        "20260408-101010-1",
        audit_text="# Session Audit: x\n",
    )
    builder = SessionRetroBuilder(project)
    retro = builder.build("session_20260408-101010-1")
    assert retro.session_id == "20260408-101010-1"


def test_programmatic_parses_git_artifacts(tmp_path: Path) -> None:
    """Real git repo with a fake commit after session start → files_changed populated."""
    # init git repo
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=tmp_path, check=True
    )
    # session starts in the past
    session_id = "20260101-000000-1"
    _make_project_with_session(
        tmp_path,
        session_id,
        audit_text="# Session Audit\n",
        metrics={"role": "test", "turns": 1, "tool_calls": 0, "exit_code": 0},
    )
    # create a commit "after" session start
    (tmp_path / "hello.txt").write_text("hi")
    subprocess.run(["git", "add", "hello.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "test commit"],
        cwd=tmp_path,
        check=True,
    )

    builder = SessionRetroBuilder(tmp_path)
    retro = builder.build(session_id)
    assert "hello.txt" in retro.artifacts.files_changed
    assert any("test commit" in c for c in retro.artifacts.commits)


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.t"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=repo, check=True
    )


def _commit_at(repo: Path, filename: str, iso_ts: str) -> None:
    """Create a commit with both author-date and commit-date pinned to iso_ts."""
    (repo / filename).write_text("x")
    subprocess.run(["git", "add", filename], cwd=repo, check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": iso_ts,
        "GIT_COMMITTER_DATE": iso_ts,
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", f"add {filename}"],
        cwd=repo,
        check=True,
        env=env,
    )


def test_session_window_filters_files_outside_window(tmp_path: Path) -> None:
    """HATS-212: only commits inside [start, end] window appear in artifacts."""
    _git_init(tmp_path)
    session_id = "20260101-120000-1"  # start = 2026-01-01 12:00:00 UTC
    _make_project_with_session(
        tmp_path,
        session_id,
        audit_text="# Session Audit\n",
        metrics={
            "role": "test",
            "turns": 1,
            "tool_calls": 0,
            "exit_code": 0,
            "duration_s": 60,  # end = 12:01:00 UTC
        },
    )

    _commit_at(tmp_path, "inside.txt", "2026-01-01T12:00:30+00:00")
    _commit_at(tmp_path, "after.txt", "2026-01-01T12:05:00+00:00")

    builder = SessionRetroBuilder(tmp_path)
    retro = builder.build(session_id)

    assert "inside.txt" in retro.artifacts.files_changed
    assert "after.txt" not in retro.artifacts.files_changed
    assert any("inside.txt" in c for c in retro.artifacts.commits)
    assert not any("after.txt" in c for c in retro.artifacts.commits)


def test_session_with_zero_in_window_commits(tmp_path: Path) -> None:
    """HATS-212: 0-commit session must produce empty files_changed/commits."""
    _git_init(tmp_path)
    session_id = "20260101-120000-1"
    _make_project_with_session(
        tmp_path,
        session_id,
        audit_text="# Session Audit\n",
        metrics={
            "role": "test",
            "turns": 1,
            "tool_calls": 0,
            "exit_code": 0,
            "duration_s": 30,  # end = 12:00:30 UTC
        },
    )

    _commit_at(tmp_path, "before.txt", "2026-01-01T11:59:00+00:00")
    _commit_at(tmp_path, "after.txt", "2026-01-01T12:05:00+00:00")

    builder = SessionRetroBuilder(tmp_path)
    retro = builder.build(session_id)

    assert retro.artifacts.files_changed == []
    assert retro.artifacts.commits == []


def test_tasks_closed_filtered_by_window(tmp_path: Path) -> None:
    """HATS-212: only tasks with `updated` inside [start, end] are reported."""
    _git_init(tmp_path)
    session_id = "20260101-120000-1"
    _make_project_with_session(
        tmp_path,
        session_id,
        audit_text="# Session Audit\n",
        metrics={
            "role": "test",
            "turns": 1,
            "tool_calls": 0,
            "exit_code": 0,
            "duration_s": 600,  # end = 12:10:00 UTC
        },
    )

    # Minimal ai-hats.yaml so resolve_task_prefix has a writable target.
    (tmp_path / "ai-hats.yaml").write_text("task_prefix: TST\n")

    tasks_dir = tmp_path / ".agent" / "backlog" / "tasks"
    inside_dir = tasks_dir / "TST-001"
    after_dir = tasks_dir / "TST-002"
    inside_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    (inside_dir / "task.yaml").write_text(
        "id: TST-001\n"
        "title: inside\n"
        "state: done\n"
        "updated: '2026-01-01T12:05:00Z'\n"
    )
    (after_dir / "task.yaml").write_text(
        "id: TST-002\n"
        "title: after\n"
        "state: done\n"
        "updated: '2026-01-01T13:00:00Z'\n"
    )

    builder = SessionRetroBuilder(tmp_path)
    retro = builder.build(session_id)

    assert retro.artifacts.tasks_closed == ["TST-001"]


def test_build_and_save_writes_to_mode_subdir(project: Path) -> None:
    _make_project_with_real_fixture(project)
    builder = SessionRetroBuilder(project)
    path = builder.build_and_save(FIXTURE_SESSION_ID, mode=BuilderMode.PROGRAMMATIC)
    expected = (
        project
        / ".agent"
        / "retrospectives"
        / "sessions"
        / "programmatic"
        / f"{FIXTURE_SESSION_ID}.md"
    )
    assert path == expected
    assert path.exists()


def test_build_and_save_separates_programmatic_and_llm(project: Path) -> None:
    """Each mode writes to its own subdir; the two files coexist."""
    _make_project_with_real_fixture(project)
    builder = SessionRetroBuilder(
        project,
        llm_caller=lambda _: "SUMMARY: Did X.\nOBSERVATIONS:\n- a\n",
    )
    p_path = builder.build_and_save(FIXTURE_SESSION_ID, mode=BuilderMode.PROGRAMMATIC)
    l_path = builder.build_and_save(FIXTURE_SESSION_ID, mode=BuilderMode.LLM)
    assert p_path != l_path
    assert p_path.parent.name == "programmatic"
    assert l_path.parent.name == "llm"
    assert p_path.exists() and l_path.exists()


def test_build_and_save_output_roundtrips_through_loader(project: Path) -> None:
    _make_project_with_real_fixture(project)
    builder = SessionRetroBuilder(project)
    path = builder.build_and_save(FIXTURE_SESSION_ID)
    loaded, body = load(path)
    assert isinstance(loaded, SessionRetroV1)
    assert loaded.session_id == FIXTURE_SESSION_ID
    assert "Session Retro" in body


def test_builder_fails_on_missing_session(project: Path) -> None:
    builder = SessionRetroBuilder(project)
    with pytest.raises(FileNotFoundError):
        builder.build("9999-9999-9")


# --- llm / hybrid modes ---


def test_llm_mode_uses_injected_caller(project: Path) -> None:
    _make_project_with_real_fixture(project)
    captured: dict = {}

    def fake(prompt: str) -> str:
        captured["prompt"] = prompt
        return (
            "SUMMARY: Did the X thing successfully.\n"
            "OBSERVATIONS:\n"
            "- used Grep before Glob\n"
            "- retried Edit after Read-before-Edit error\n"
        )

    builder = SessionRetroBuilder(project, llm_caller=fake)
    retro = builder.build(FIXTURE_SESSION_ID, mode=BuilderMode.LLM)
    assert retro.summary == "Did the X thing successfully."
    assert retro.observations == [
        "used Grep before Glob",
        "retried Edit after Read-before-Edit error",
    ]
    assert "AUDIT" in captured["prompt"]
    assert "METRICS" in captured["prompt"]


def test_llm_mode_robust_to_extra_text(project: Path) -> None:
    _make_project_with_real_fixture(project)

    def fake(_: str) -> str:
        return (
            "Sure, here is the analysis:\n\n"
            "SUMMARY: A short summary.\n"
            "OBSERVATIONS:\n"
            "- single observation\n"
            "\n"
            "Note: this section is ignored.\n"
        )

    builder = SessionRetroBuilder(project, llm_caller=fake)
    retro = builder.build(FIXTURE_SESSION_ID, mode=BuilderMode.LLM)
    assert retro.summary == "A short summary."
    assert retro.observations == ["single observation"]


def test_llm_mode_without_caller_raises(project: Path) -> None:
    _make_project_with_real_fixture(project)
    builder = SessionRetroBuilder(project)  # no llm_caller
    with pytest.raises(RuntimeError, match="llm/hybrid"):
        builder.build(FIXTURE_SESSION_ID, mode=BuilderMode.LLM)


def test_llm_mode_disabled_by_env(monkeypatch, project: Path) -> None:
    _make_project_with_real_fixture(project)
    monkeypatch.setenv("AI_HATS_NO_LLM", "1")
    builder = SessionRetroBuilder(project, llm_caller=lambda p: "SUMMARY: x")
    with pytest.raises(RuntimeError, match="AI_HATS_NO_LLM"):
        builder.build(FIXTURE_SESSION_ID, mode=BuilderMode.LLM)


def test_summary_includes_files_when_no_git(project: Path) -> None:
    """Without git, summary still produces the basic line."""
    _make_project_with_session(
        project,
        "20260408-101010-1",
        audit_text="# x\n",
        metrics={"role": "test", "turns": 3, "tool_calls": 5, "exit_code": 0},
    )
    builder = SessionRetroBuilder(project)
    retro = builder.build("20260408-101010-1")
    assert "3 turn(s), 5 tool call(s)" in retro.summary
