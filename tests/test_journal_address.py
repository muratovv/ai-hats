"""Where a guard-journal record lands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PY = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/hooks/bypass_journal.py"
ENV_JOURNAL_DIR = "AI_HATS_JOURNAL_DIR"


@pytest.fixture
def a_repo(tmp_path: Path) -> Path:
    """A real git repo — the writer asks git where the common dir is."""
    project = tmp_path / "proj"
    project.mkdir()
    for args in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.email", "t@e.x"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(args, cwd=str(project), check=True)  # noqa: S603, S607
    return project


def _record(cwd: Path, *, kind: str = "hatch", **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(PY),
            "record",
            "--kind",
            kind,
            "--reason",
            "AI_HATS_TEST_OFF",
            "--hook",
            "probe.py",
        ],
        cwd=str(cwd),
        env={
            k: v for k, v in {**os.environ, **env_extra}.items() if k != "AI_HATS_JOURNAL_LIVE_GIT"
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_the_redirect_decides_where_the_record_lands(tmp_path: Path, a_repo: Path) -> None:
    elsewhere = tmp_path / "journal-home"
    proc = _record(a_repo, **{ENV_JOURNAL_DIR: str(elsewhere)})
    assert proc.returncode == 0, proc.stderr
    redirected = elsewhere / "bypasses.jsonl"
    assert redirected.is_file(), proc.stderr
    assert json.loads(redirected.read_text().splitlines()[-1])["reason"] == "AI_HATS_TEST_OFF"
    assert not (a_repo / ".git" / "ai-hats" / "bypasses.jsonl").exists()


def test_without_a_redirect_the_record_lands_in_the_repository(a_repo: Path) -> None:
    proc = _record(a_repo, **{ENV_JOURNAL_DIR: ""})
    assert proc.returncode == 0, proc.stderr
    landed = a_repo / ".git" / "ai-hats" / "bypasses.jsonl"
    assert landed.is_file(), proc.stderr
    assert json.loads(landed.read_text().splitlines()[-1])["reason"] == "AI_HATS_TEST_OFF"


def test_an_explicit_cwd_is_not_stolen_by_the_redirect(tmp_path: Path, monkeypatch) -> None:
    """In-process callers that name a project keep that project."""
    import importlib.util

    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)  # noqa: S603, S607
    spec = importlib.util.spec_from_file_location("bypass_journal_under_test", PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv(ENV_JOURNAL_DIR, str(sandbox))
    ok = module.journal_bypass("hatch", "AI_HATS_TEST_OFF", hook="probe.py", cwd=project)
    assert ok is True
    assert (project / ".git" / "ai-hats" / "bypasses.jsonl").is_file()
    assert not (sandbox / "bypasses.jsonl").exists()


def _record_marked(cwd: Path, sandbox: Path, live: Path) -> subprocess.CompletedProcess:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AI_HATS_JOURNAL_DIR", "AI_HATS_JOURNAL_LIVE_GIT")
    }
    env[ENV_JOURNAL_DIR] = str(sandbox)
    env["AI_HATS_JOURNAL_LIVE_GIT"] = str(live)
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(PY),
            "record",
            "--kind",
            "fail_open",
            "--reason",
            "unparsable payload",
            "--hook",
            "probe.py",
        ],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_the_live_marker_keeps_another_repository(tmp_path: Path, a_repo: Path) -> None:
    sandbox = tmp_path / "sandbox"
    proc = _record_marked(a_repo, sandbox, tmp_path / "other.git")
    assert proc.returncode == 0, proc.stderr
    landed = a_repo / ".git" / "ai-hats" / "bypasses.jsonl"
    assert landed.is_file(), proc.stderr
    assert json.loads(landed.read_text().splitlines()[-1])["kind"] == "fail-open"
    assert not (sandbox / "bypasses.jsonl").exists()


def test_the_live_marker_leaves_a_directory_with_no_git_loud(tmp_path: Path) -> None:
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    sandbox = tmp_path / "sandbox"
    proc = _record_marked(nowhere, sandbox, tmp_path / "other.git")
    assert proc.returncode == 1
    assert "fail-open" in proc.stderr
    assert not (sandbox / "bypasses.jsonl").exists()


def test_fail_open_is_stored_as_fail_hyphen_open(tmp_path: Path, a_repo: Path) -> None:
    proc = _record(a_repo, kind="fail_open", **{ENV_JOURNAL_DIR: str(tmp_path / "j")})
    assert proc.returncode == 0, proc.stderr
    row = json.loads((tmp_path / "j" / "bypasses.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == "fail-open"


def test_a_later_kind_is_still_recorded(tmp_path: Path, a_repo: Path) -> None:
    proc = _record(a_repo, kind="consent", **{ENV_JOURNAL_DIR: str(tmp_path / "j")})
    assert proc.returncode == 0, proc.stderr
    row = json.loads((tmp_path / "j" / "bypasses.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == "consent"
