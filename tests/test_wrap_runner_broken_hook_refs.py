"""HATS-1509 / HATS-1522: seam tests for ``WrapRunner._check_broken_hook_refs``.

The remedy differs by ownership: ai-hats can clean up its own entries and must
not promise that for a user's. HATS-1522 adds what the message owes the reader —
what broke, what to run, and what the run changes beyond the repair — because
this text is the only instruction anyone gets. Detection itself is covered in
``tests/test_migration_assert.py``.
"""

import json
from types import SimpleNamespace

from ai_hats.constants import HOOK_PRE_TOOL_USE
from ai_hats.paths import runs_dir
from ai_hats.wrap_runner import WrapRunner
from ai_hats_core.layout import ProjectLayout


def _runner(project):
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats.hooks_manager import HooksManager
    from ai_hats.models import ProjectConfig
    from ai_hats_observe import SessionManager, SidecarTracer
    from ai_hats_core import CompositionResult

    hooks = HooksManager(
        project,
        ProjectConfig(),
        resolve_provider=lambda name: None,
    )
    payload = CompositionPayload(
        result=CompositionResult(name="t", priorities=[], rules=[], skills=[], injections=[]),
        provider=None,
        effective_role="t",
        hooks=hooks,
    )
    return WrapRunner(
        ProjectLayout.at(project),
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


def test_managed_broken_ref_names_the_command_that_heals_locally(tmp_path):
    _seed_claude_hook(tmp_path, "/nowhere/guard.sh", tag="ai-hats:hats-437")
    traces: list[str] = []

    notices = _runner(tmp_path)._check_broken_hook_refs(_session(traces))

    assert [n.level for n in notices] == ["warn"]
    text = notices[0].text
    assert "/nowhere/guard.sh" in text
    assert "ai-hats self init --no-wizard" in text
    assert str(tmp_path) in text, "the command is useless without the dir to run it in"
    # HATS-1522: `self update` reinstalls the harness from GitHub — on an
    # editable install that is somebody's working checkout.
    assert "self update" not in text
    assert traces  # finding logged to the session


def test_the_command_stands_alone_on_a_copyable_line(tmp_path):
    """House format for a remedy (``cli/_helpers.py``, ``migration_backup.py``):
    a ``Fix:``/``Recovery:`` line carrying one self-contained command. Buried
    mid-sentence it cannot be copied without editing."""
    _seed_claude_hook(tmp_path, "/nowhere/guard.sh", tag="ai-hats:hats-437")

    text = _runner(tmp_path)._check_broken_hook_refs(_session([]))[0].text

    fix = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("Fix:")]
    assert len(fix) == 1, f"expected exactly one Fix: line, got {fix}"
    assert fix[0] == f"Fix: cd {tmp_path} && ai-hats self init --no-wizard"


def test_managed_remedy_says_what_the_command_changes_beyond_the_repair(tmp_path):
    """Q9.3 — the install-time path also runs migrations. Learning that from
    the diff afterwards is the failure this text exists to prevent."""
    _seed_claude_hook(tmp_path, "/nowhere/guard.sh", tag="ai-hats:hats-437")

    text = _runner(tmp_path)._check_broken_hook_refs(_session([]))[0].text

    assert "migration" in text.lower()
    assert "harness reports an error" in text, "the reader must know what breaks today"


def test_remedy_avoids_ai_hats_internal_vocabulary(tmp_path):
    """Q9 — 'managed entry' / 'reclaim' / 'owner' mean nothing outside the repo."""
    _seed_claude_hook(tmp_path, "/nowhere/guard.sh", tag="ai-hats:hats-437")

    text = _runner(tmp_path)._check_broken_hook_refs(_session([]))[0].text.lower()

    for jargon in ("reclaim", "managed entry", "owner", "surface"):
        assert jargon not in text, f"internal vocabulary leaked into the user message: {jargon}"


def test_many_broken_refs_collapse_into_one_instruction(tmp_path):
    """Six refs used to print the same three-line remedy six times."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    HOOK_PRE_TOOL_USE: [
                        {
                            "matcher": "Bash",
                            "_ai_hats_managed": f"ai-hats:guard-{i}",
                            "hooks": [{"type": "command", "command": f"/nowhere/guard-{i}.sh"}],
                        }
                        for i in range(3)
                    ]
                }
            }
        )
    )

    notices = _runner(tmp_path)._check_broken_hook_refs(_session([]))

    assert len(notices) == 1, "one instruction, not one per finding"
    for i in range(3):
        assert f"/nowhere/guard-{i}.sh" in notices[0].text
    assert notices[0].text.count("ai-hats self init --no-wizard") == 1


def test_user_owned_broken_ref_is_not_promised_an_ai_hats_fix(tmp_path):
    _seed_claude_hook(tmp_path, "/nowhere/mine.sh")

    notices = _runner(tmp_path)._check_broken_hook_refs(_session([]))

    assert [n.level for n in notices] == ["warn"]
    assert "/nowhere/mine.sh" in notices[0].text
    assert "self update" not in notices[0].text
    assert "self init" not in notices[0].text


def test_mixed_ownership_splits_into_two_instructions(tmp_path):
    """One remedy cannot serve both: ai-hats cleans up only what it wrote."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    HOOK_PRE_TOOL_USE: [
                        {
                            "matcher": "Bash",
                            "_ai_hats_managed": "ai-hats:hats-437",
                            "hooks": [{"type": "command", "command": "/nowhere/ours.sh"}],
                        },
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "/nowhere/theirs.sh"}],
                        },
                    ]
                }
            }
        )
    )

    texts = [n.text for n in _runner(tmp_path)._check_broken_hook_refs(_session([]))]

    assert len(texts) == 2
    ours = [t for t in texts if "/nowhere/ours.sh" in t]
    theirs = [t for t in texts if "/nowhere/theirs.sh" in t]
    assert len(ours) == 1 and len(theirs) == 1
    assert "ai-hats self init --no-wizard" in ours[0]
    assert "self init" not in theirs[0]


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
