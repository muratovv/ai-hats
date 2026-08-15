from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats.pipeline.steps.compute_usage import ComputeUsage
from ai_hats.runtime import _finalize_sub_agent
from ai_hats.runtime_common import _flag_sensor_error
from ai_hats_observe import AuditWriter, SessionManager
from ai_hats_observe.artifacts import REASONING_LOG, TRANSCRIPT_TXT, USAGE_JSON
from ai_hats_observe.cli import _seam
from ai_hats_observe.cli.session import _backfill_one


@contextmanager
def _umask(mask: int):
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_subagent_finalize_keeps_outputs_private_under_permissive_umask(
    tmp_path: Path,
) -> None:
    with _umask(0o022):
        session = SessionManager(runs_dir=tmp_path / "runs").create_session()
        session.init_audit(role="maintainer", provider="codex")
        _finalize_sub_agent(
            session,
            role="maintainer",
            provider="codex",
            model="gpt-5",
            isolation_mode="worktree",
            exit_code=1,
            stdout="sensitive transcript",
            stderr="sensitive reasoning",
        )

    artifacts = (
        session.session_dir / TRANSCRIPT_TXT,
        session.session_dir / REASONING_LOG,
        session.audit_path,
        session.metrics_path,
    )
    assert {path.name: _mode(path) for path in artifacts} == {
        path.name: 0o600 for path in artifacts
    }


def test_sensor_error_rewrite_tightens_metrics_permissions(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    session.init_audit(role="maintainer", provider="codex")
    session.metrics_path.chmod(0o644)

    with _umask(0o022):
        _flag_sensor_error(session)

    assert _mode(session.metrics_path) == 0o600
    assert "sensor-error" in json.loads(session.metrics_path.read_text())["flags"]


def test_compute_usage_writes_private_artifact(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    session.init_audit(role="maintainer", provider="codex")
    source = tmp_path / "transcript.jsonl"
    source.write_text("{}\n")
    parser = SimpleNamespace(parse_usage=lambda _jsonl, _trace: {"schema_version": "usage/v1"})

    with _umask(0o022):
        delta = ComputeUsage().run(
            session_id=session.session_id,
            session_dir=session.session_dir,
            claude_session_id="provider-session",
            project_dir=tmp_path,
            transcript_resolver=lambda *_args, **_kwargs: source,
            audit_writer_factory=lambda: SimpleNamespace(parser=parser),
        )

    usage_path = session.session_dir / USAGE_JSON
    assert delta == {"usage_path": usage_path}
    assert _mode(usage_path) == 0o600


def test_backfill_identity_rewrite_stays_private_when_audit_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs").create_session()
    session.init_audit(role="maintainer", provider="claude")
    session.finalize_audit({"exit_code": 0})
    provider_session_id = "5c639a19-5b64-4a91-8813-2937b47e9126"
    session.log_trace(
        "[SYS]",
        f"Launching: claude --settings settings.json --session-id {provider_session_id}",
    )
    session.metrics_path.chmod(0o644)
    source = tmp_path / f"{provider_session_id}.jsonl"
    source.write_text("{}\n")

    def resolver(*_args, **_kwargs):
        return source

    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", lambda _provider: (resolver, None))

    def fail_build(*_args, **_kwargs):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(AuditWriter, "build", fail_build)

    with _umask(0o022), pytest.raises(RuntimeError, match="audit failed"):
        _backfill_one(session, project_dir=tmp_path, dry_run=False)

    assert _mode(session.metrics_path) == 0o600
    metrics = json.loads(session.metrics_path.read_text())
    assert metrics["claude_session_id"] == provider_session_id
