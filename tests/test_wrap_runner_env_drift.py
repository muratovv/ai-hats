"""HATS-1013: seam tests for ``WrapRunner._lint_env_drift``.

Mirrors test_wrap_runner_settings_lint.py: drives the producer directly (a
real run needs a PTY spawn); the detector itself is covered in
tests/test_env_drift.py — here it is monkeypatched.
"""

from types import SimpleNamespace

from ai_hats.paths import runs_dir
from ai_hats.wrap_runner import WrapRunner


def _runner(project):
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats.hooks_manager import HooksManager
    from ai_hats.models import ProjectConfig
    from ai_hats_observe import SessionManager, SidecarTracer
    from ai_hats_core import CompositionResult

    hooks = HooksManager(
        project,
        ProjectConfig(),
        compose=lambda role: None,
        resolve_provider=lambda name: None,
    )
    payload = CompositionPayload(
        result=CompositionResult(name="t", priorities=[], rules=[], skills=[], injections=[]),
        provider=None,
        effective_role="t",
        hooks=hooks,
    )
    return WrapRunner(
        project,
        payload,
        session_mgr=SessionManager(project, runs_dir=runs_dir(project)),
        tracer_factory=SidecarTracer,
    )


def _session(traces):
    return SimpleNamespace(session_id="sess-1", log_sys=lambda msg: traces.append(msg))


def test_drift_findings_become_warn_notices(tmp_path, monkeypatch):
    import ai_hats.env_drift

    monkeypatch.setattr(
        ai_hats.env_drift, "stale_dev_env_warnings", lambda: ["dev env outdated — run 'x'"]
    )
    traces: list[str] = []

    notices = _runner(tmp_path)._lint_env_drift(_session(traces))

    assert [(n.level, n.text) for n in notices] == [("warn", "dev env outdated — run 'x'")]
    assert traces  # findings logged to the session


def test_in_sync_env_yields_no_notices(tmp_path, monkeypatch):
    import ai_hats.env_drift

    monkeypatch.setattr(ai_hats.env_drift, "stale_dev_env_warnings", lambda: [])

    assert _runner(tmp_path)._lint_env_drift(_session([])) == []


def test_drift_lint_failure_is_fail_open(tmp_path, monkeypatch):
    import ai_hats.env_drift

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(ai_hats.env_drift, "stale_dev_env_warnings", _boom)
    traces: list[str] = []

    assert _runner(tmp_path)._lint_env_drift(_session(traces)) == []
    assert any("env-drift" in t for t in traces)
