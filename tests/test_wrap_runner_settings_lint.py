"""HATS-1006: seam tests for ``WrapRunner._lint_provider_settings``.

Drives the producer directly (a real run needs a PTY spawn); the Claude lint
itself is covered in ``tests/test_claude_settings_lint.py`` — here the provider
is a stub, proving the runner stays surface-agnostic.
"""

from types import SimpleNamespace

from ai_hats.paths import runs_dir
from ai_hats.wrap_runner import WrapRunner


def _runner(project, provider):
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
        provider=provider,
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


def test_provider_findings_become_warn_notices(tmp_path):
    provider = SimpleNamespace(settings_lint_warnings=lambda project_dir: ["w1", "w2"])
    traces: list[str] = []

    notices = _runner(tmp_path, provider)._lint_provider_settings(_session(traces))

    assert [(n.level, n.text) for n in notices] == [("warn", "w1"), ("warn", "w2")]
    assert traces  # findings logged to the session


def test_clean_provider_yields_no_notices(tmp_path):
    provider = SimpleNamespace(settings_lint_warnings=lambda project_dir: [])

    assert _runner(tmp_path, provider)._lint_provider_settings(_session([])) == []


def test_none_provider_is_skipped(tmp_path):
    assert _runner(tmp_path, None)._lint_provider_settings(_session([])) == []


def test_provider_lint_failure_is_fail_open(tmp_path):
    def _boom(project_dir):
        raise RuntimeError("boom")

    provider = SimpleNamespace(settings_lint_warnings=_boom)

    assert _runner(tmp_path, provider)._lint_provider_settings(_session([])) == []
