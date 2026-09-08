"""e2e (HATS-1897)

flow: a consent question precedes a denying guard on the MCP command path
cmds:
    rack transition HATS-1897 execute
expect: the later denial wins and no authorization is issued
why: accepting the first ask must not hide another guard's refusal
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

from ai_hats.surfaces.hook_channel import ChainDecision, HookRow

pytestmark = pytest.mark.integration


def test_later_guard_denial_wins_over_consent_question(tmp_path: Path) -> None:
    from ai_hats.consent_mcp.guards import check_transition

    rows = []
    for name, decision in (("safety_gate.py", "ask"), ("quality.py", "deny")):
        hook = tmp_path / name
        reply = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": name,
            }
        }
        hook.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({reply!r}))\n")
        hook.chmod(0o755)
        tag = "ai-hats:safety-guard:PreToolUse:Bash" if decision == "ask" else name
        rows.append(HookRow(command=hook, matcher="Bash", tag=tag))

    verdict = check_transition(
        ("transition", "HATS-1897", "execute"),
        project_dir=tmp_path,
        rows=rows,
        environ={},
    )

    assert verdict.decision is ChainDecision.DENY
    assert "quality.py" in verdict.reason


def test_materialized_guard_defers_question_without_ticket(tmp_path: Path, monkeypatch) -> None:
    from _helpers.codex_consent import session
    from ai_hats.consent_mcp.guards import check_transition
    from ai_hats.surfaces.codex.hook_dispatcher import _load_manifest, _rows
    from ai_hats.surfaces.hook_channel import HookEvent
    from ai_hats_library.hooks.consent_ticket import tickets_dir

    project, env = session(tmp_path, monkeypatch)
    rows = _rows(_load_manifest(env), HookEvent.PRE_TOOL_USE)
    assert any("safety-guard" in row.tag for row in rows)

    verdict = check_transition(
        ("transition", "HATS-1897", "execute"),
        project_dir=project,
        rows=rows,
        environ=env,
    )

    assert verdict.decision is ChainDecision.ASK, verdict
    assert verdict.updated_input is None
    store = tickets_dir(project)
    assert store is not None
    assert not list(store.glob("*.json"))


def test_direct_server_launch_is_refused(tmp_path: Path, monkeypatch) -> None:
    from _helpers.codex_consent import session
    from _helpers.hook_chain import run_codex_dispatch

    project, env = session(tmp_path, monkeypatch)
    result = run_codex_dispatch(
        project,
        env,
        tool_input={"command": "python -m ai_hats.consent_mcp.server"},
    )
    assert result.returncode == 0, result.stderr
    reply = json.loads(result.stdout)["hookSpecificOutput"]
    assert reply["permissionDecision"] == "deny"
    assert "registered MCP tool" in reply["permissionDecisionReason"]


def test_shell_transition_refusal_points_to_registered_tool(tmp_path: Path, monkeypatch) -> None:
    from _helpers.codex_consent import session
    from _helpers.hook_chain import run_codex_dispatch

    project, env = session(tmp_path, monkeypatch)
    result = run_codex_dispatch(
        project,
        env,
        tool_input={"command": "rack transition HATS-1897 execute"},
    )
    reply = json.loads(result.stdout)["hookSpecificOutput"]
    assert reply["permissionDecision"] == "deny"
    assert "ai_hats_consent.rack_transition" in reply["permissionDecisionReason"]


def test_codex_marker_does_not_change_claude_ticket_path(tmp_path: Path, monkeypatch) -> None:
    from _helpers.codex_consent import session
    from ai_hats.consent_mcp.guards import check_transition
    from ai_hats.surfaces.codex.hook_dispatcher import _load_manifest, _rows
    from ai_hats.surfaces.hook_channel import HookEvent

    project, env = session(tmp_path, monkeypatch)
    identity = json.loads(env["AI_HATS_SESSION_IDENTITY"])
    identity["provider"] = "claude"
    env["AI_HATS_SESSION_IDENTITY"] = json.dumps(identity)
    answer = check_transition(
        ("transition", "HATS-1897", "execute"),
        project_dir=project,
        rows=_rows(_load_manifest(env), HookEvent.PRE_TOOL_USE),
        environ=env,
    )
    assert answer.decision is ChainDecision.DENY
    assert "Unsupported guard question" in answer.reason
    assert list((project / ".git/ai-hats/consent").glob("*.json"))
