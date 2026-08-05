"""HATS-1509: seam tests for ``WrapRunner._check_broken_hook_refs``.

The remedy differs by ownership: ``self update`` reclaims an ``ai-hats:``-tagged
entry, and must not be promised for a user's own. Detection itself is covered in
``tests/test_migration_assert.py``.
"""

import json
from types import SimpleNamespace

from ai_hats.constants import HOOK_PRE_TOOL_USE
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


def _seed_claude_hook(project, command: str, tag: str | None = None) -> None:
    entry: dict = {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}
    if tag is not None:
        entry["_ai_hats_managed"] = tag
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"hooks": {HOOK_PRE_TOOL_USE: [entry]}}))


def test_managed_broken_ref_warns_with_the_self_update_remedy(tmp_path):
    _seed_claude_hook(tmp_path, "/nowhere/guard.sh", tag="ai-hats:hats-437")
    traces: list[str] = []

    notices = _runner(tmp_path)._check_broken_hook_refs(_session(traces))

    assert [n.level for n in notices] == ["warn"]
    assert "/nowhere/guard.sh" in notices[0].text
    assert "ai-hats self update" in notices[0].text
    assert traces  # finding logged to the session


def test_user_owned_broken_ref_is_not_promised_a_self_update_fix(tmp_path):
    _seed_claude_hook(tmp_path, "/nowhere/mine.sh")

    notices = _runner(tmp_path)._check_broken_hook_refs(_session([]))

    assert [n.level for n in notices] == ["warn"]
    assert "/nowhere/mine.sh" in notices[0].text
    assert "self update" not in notices[0].text


def test_resolving_refs_yield_no_notices(tmp_path):
    script = tmp_path / "guard.sh"
    script.write_text("#!/bin/sh\n")
    _seed_claude_hook(tmp_path, str(script), tag="ai-hats:hats-437")

    assert _runner(tmp_path)._check_broken_hook_refs(_session([])) == []


def test_detector_failure_is_fail_open(tmp_path, monkeypatch):
    from ai_hats import migration_assert

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(migration_assert, "find_broken_hook_refs", _boom)
    traces: list[str] = []

    assert _runner(tmp_path)._check_broken_hook_refs(_session(traces)) == []
    assert any("FAILED" in t for t in traces), "a swallowed failure must still be logged"
