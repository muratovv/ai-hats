"""HATS-1006: seam tests for ``WrapRunner._lint_claude_settings``.

Drives the producer directly (a real run needs a PTY spawn); the pure lint
logic is covered in ``tests/test_claude_settings_lint.py``.
"""

import json
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


def _seed(path, rules):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": rules}))


def test_deprecated_rules_across_chain_become_warn_notices(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    fake_home = tmp_path / "home"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("ai_hats.paths.claude.Path.home", lambda: fake_home)
    _seed(fake_home / ".claude" / "settings.json", {"allow": ["Write(~/dev/**)"]})
    _seed(project / ".claude" / "settings.json", {"deny": ["Glob(src/**)"]})
    _seed(project / ".claude" / "settings.local.json", {"allow": ["Edit(//tmp/**)"]})

    traces: list[str] = []
    notices = _runner(project)._lint_claude_settings(_session(traces))

    assert [n.level for n in notices] == ["warn", "warn"]
    assert "Write(~/dev/**)" in notices[0].text
    assert "Edit(~/dev/**)" in notices[0].text
    assert "Glob(src/**)" in notices[1].text
    assert "Read(src/**)" in notices[1].text
    assert traces  # findings logged to the session


def test_clean_chain_yields_no_notices(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("ai_hats.paths.claude.Path.home", lambda: tmp_path / "home")

    assert _runner(project)._lint_claude_settings(_session([])) == []


def test_lint_failure_is_fail_open(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()

    def _boom(paths):
        raise RuntimeError("boom")

    monkeypatch.setattr("ai_hats.wrap_runner.lint_settings_files", _boom)

    assert _runner(project)._lint_claude_settings(_session([])) == []
