from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from ai_hats.pipeline.steps.compute_usage import ComputeUsage
from ai_hats.runtime import _finalize_sub_agent
from ai_hats.runtime_common import _flag_sensor_error
from ai_hats_observe import SessionManager
from ai_hats_observe.artifacts import REASONING_LOG, TRANSCRIPT_TXT, USAGE_JSON


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
