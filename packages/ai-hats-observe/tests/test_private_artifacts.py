from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_hats_observe import SessionManager


@contextmanager
def _umask(mask: int):
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_create_session_directory_is_private_under_permissive_umask(tmp_path: Path) -> None:
    with _umask(0o022):
        session = SessionManager(runs_dir=tmp_path / "runs").create_session()

    assert _mode(session.session_dir) == 0o700


def test_atomic_artifact_replace_is_private_under_permissive_umask(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()

    with _umask(0o022):
        session.write_artifact_text(session.metrics_path, "first")

    assert _mode(session.metrics_path) == 0o600
    session.metrics_path.chmod(0o644)

    with _umask(0o022):
        session.write_artifact_text(session.metrics_path, "second")

    assert session.metrics_path.read_text() == "second"
    assert _mode(session.metrics_path) == 0o600


def test_artifact_append_creates_and_tightens_private_files(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    session.audit_path.write_text("first\n")
    session.audit_path.chmod(0o644)

    with _umask(0o022):
        session.append_artifact_text(session.trace_path, "new\n")
        session.append_artifact_text(session.audit_path, "second\n")

    assert session.trace_path.read_text() == "new\n"
    assert _mode(session.trace_path) == 0o600
    assert session.audit_path.read_text() == "first\nsecond\n"
    assert _mode(session.audit_path) == 0o600


@pytest.mark.integration
def test_full_session_lifecycle_keeps_sensitive_artifacts_private(tmp_path: Path) -> None:
    with _umask(0o022):
        session = SessionManager(runs_dir=tmp_path / "runs").create_session()
        session.init_audit(role="maintainer", provider="codex")
        session.log_trace("[SYS]", "started")
        session.append_audit("running")
        session.save_meta_prompt("developer prompt")
        session.save_role_materialization({"prompt": "developer prompt"})
        session.finalize_audit({"exit_code": 1})
        session.finalize_audit({"exit_code": 0})

    artifacts = (
        session.meta_prompt_path,
        session.role_materialization_path,
        session.trace_path,
        session.audit_path,
        session.metrics_path,
    )
    assert {path.name: _mode(path) for path in artifacts} == {
        path.name: 0o600 for path in artifacts
    }
