from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_hats_observe import AuditWriter, SessionManager, SidecarTracer
from ai_hats_observe.artifacts import TRANSCRIPT_JSONL


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


@pytest.mark.integration
def test_audit_rebuild_keeps_outputs_private(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    session.init_audit(role="maintainer", provider="codex")
    session.audit_path.chmod(0o644)
    session.metrics_path.chmod(0o644)

    with _umask(0o022):
        AuditWriter().build(session)

    assert _mode(session.audit_path) == 0o600
    assert _mode(session.metrics_path) == 0o600


def test_preserved_transcript_is_streamed_to_private_artifact(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    source = tmp_path / "source.jsonl"
    source.write_text('{"type": "message"}\n')
    destination = session.session_dir / TRANSCRIPT_JSONL
    destination.write_text("stale\n")
    destination.chmod(0o644)

    with _umask(0o022):
        preserved = AuditWriter._preserve_transcript(session, source)

    assert preserved is True
    assert destination.read_text() == source.read_text()
    assert _mode(destination) == 0o600


def test_preserving_transcript_already_in_session_does_not_truncate_it(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    destination = session.session_dir / TRANSCRIPT_JSONL
    destination.write_text('{"type": "message"}\n')
    destination.chmod(0o644)

    with _umask(0o022):
        preserved = AuditWriter._preserve_transcript(session, destination)

    assert preserved is True
    assert destination.read_text() == '{"type": "message"}\n'
    assert _mode(destination) == 0o600


def test_merged_transcript_is_written_as_private_artifact(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text('{"type": "first"}\n')
    second.write_text('{"type": "second"}\n')

    with _umask(0o022):
        preserved = AuditWriter._preserve_transcript(session, [first, second])

    destination = session.session_dir / TRANSCRIPT_JSONL
    assert preserved is True
    assert [json.loads(line)["type"] for line in destination.read_text().splitlines()] == [
        "first",
        "second",
    ]
    assert _mode(destination) == 0o600


def test_sidecar_raw_dump_tightens_binary_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_HATS_PTY_RAW_DUMP", "1")
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    session.pty_raw_path.write_bytes(b"stale")
    session.pty_raw_path.chmod(0o644)
    tracer = SidecarTracer(session)

    with _umask(0o022):
        tracer._raw_dump(b"<<", b"secret")

    assert tracer._raw_fp not in (None, False)
    tracer._raw_fp.close()
    assert _mode(session.pty_raw_path) == 0o600
    assert b"secret" in session.pty_raw_path.read_bytes()
