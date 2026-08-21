"""Tests for ai-hats-hook-dispatcher in AGY surface plugin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats_agy.hook_dispatcher import HOOK_TIMEOUT_S, _hook_timeout, dispatch_hook


def _in_session(monkeypatch, session_id: str, project: Path) -> None:
    """A session as its launcher writes it — the envelope AND its scalars.

    Written out rather than imported from ``SessionIdentity``: the dispatcher
    reads the wire, so its tests describe the wire (ADR-0025 D1).
    """
    monkeypatch.setenv(
        "AI_HATS_SESSION_IDENTITY",
        json.dumps(
            {
                "v": 1,
                "id": session_id,
                "role": "maintainer",
                "provider": "agy",
                "project_dir": str(project),
                "session_dir": str(project / "session"),
                "skills_root": "",
            }
        ),
    )
    monkeypatch.setenv("AI_HATS_SESSION_ID", session_id)
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(project))


def test_dispatcher_noop_when_session_id_missing(monkeypatch) -> None:
    monkeypatch.delenv("AI_HATS_SESSION_IDENTITY", raising=False)
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)

    res = dispatch_hook("PreToolUse")
    assert res == 0


def test_dispatcher_says_so_when_the_pinned_manifest_is_gone(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Pin set + no manifest is a reclaimed cache dir, not a hook-less session.

    The builder writes the manifest with every pin, so the two cases are never
    the same event — and a TTL sweep reclaiming a live session's dir (HATS-1339)
    silenced every guard while exit 0 kept saying "nothing configured". Exit
    stays 0 because this gate runs ahead of every tool call on a detached
    surface (ADR-0020 D1, fail-open): refusing would kill the very session the
    diagnostic exists to rescue.
    """
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "cache"

    _in_session(monkeypatch, "sid-test", project)
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(cache_dir))

    res = dispatch_hook("PreToolUse")
    assert res == 0
    complained = capsys.readouterr().err
    assert "no hooks manifest at" in complained
    assert str(cache_dir / "hooks.json") in complained


def _seed_manifest(cache_dir: Path, hook_script: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "PreToolUse": [{"matcher": "Edit", "command": str(hook_script), "tag": "test:hook"}]
    }
    (cache_dir / "hooks.json").write_text(json.dumps(manifest))


def _marker_hook(tmp_path: Path) -> tuple[Path, Path]:
    marker_file = tmp_path / "hook_ran.txt"
    hook_script = tmp_path / "test_hook.sh"
    hook_script.write_text(f"#!/bin/sh\necho 'OK' > '{marker_file}'\n")
    hook_script.chmod(0o755)
    return marker_file, hook_script


def test_dispatcher_executes_hook_from_pinned_cache_dir(tmp_path: Path, monkeypatch) -> None:
    """The pin is the channel — the manifest lives wherever it points (HATS-1398)."""
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "out-of-tree" / "sessions" / "sid-exec"
    marker_file, hook_script = _marker_hook(tmp_path)
    _seed_manifest(cache_dir, hook_script)

    _in_session(monkeypatch, "sid-exec", project)
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(cache_dir))

    res = dispatch_hook("PreToolUse", tool_name="Edit")
    assert res == 0
    assert marker_file.read_text().strip() == "OK"


def test_a_gone_session_manifest_still_leaves_the_user_hooks_running(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The two manifests are separate channels; losing one must not disarm both."""
    home = tmp_path / "home"
    (home / ".gemini" / "config").mkdir(parents=True)
    marker_file, hook_script = _marker_hook(tmp_path)
    (home / ".gemini" / "config" / "hooks.json").write_text(
        json.dumps({"PreToolUse": [{"matcher": "*", "command": str(hook_script)}]})
    )

    monkeypatch.setenv("HOME", str(home))
    _in_session(monkeypatch, "sid-gone", tmp_path / "project")
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(tmp_path / "reclaimed"))

    res = dispatch_hook("PreToolUse", tool_name="Edit")
    assert res == 0
    assert marker_file.read_text().strip() == "OK"
    assert "no hooks manifest at" in capsys.readouterr().err


def test_dispatcher_without_the_pin_says_so_instead_of_exiting_quietly(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An ai-hats session with no pin predates the move — say it (HATS-1373 class).

    Exit 0 alone is what "this session has no hooks" looks like, so silence here
    would hide unreachable hooks rather than report them.
    """
    _in_session(monkeypatch, "sid-stale", tmp_path / "project")
    monkeypatch.delenv("AI_HATS_SESSION_CACHE_DIR", raising=False)

    res = dispatch_hook("PreToolUse", tool_name="Edit")
    assert res == 0
    assert "AI_HATS_SESSION_CACHE_DIR unset" in capsys.readouterr().err


@pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "-5", "nonsense60"])
def test_unusable_budget_override_keeps_the_default(monkeypatch, raw: str) -> None:
    """A typo in the override must not disarm the bound (HATS-1598).

    Every value here is one an operator could plausibly export; if any of them
    resolved to 0 or a negative, the hook would run unbounded again — the exact
    defect the budget exists to close, reintroduced through a config channel.
    """
    monkeypatch.setenv("AI_HATS_AGY_HOOK_TIMEOUT_S", raw)

    assert _hook_timeout() == HOOK_TIMEOUT_S


def test_positive_budget_override_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("AI_HATS_AGY_HOOK_TIMEOUT_S", "2.5")

    assert _hook_timeout() == 2.5


# --- the claude bridge, driven through the dispatcher (HATS-1776) -----------


def _agy_payload(tool: str = "run_command", command: str = "git push --force") -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "toolCall": {"name": tool, "args": {"CommandLine": command}},
        }
    )


def _recording_hook(tmp_path: Path, *, answers: str = "") -> tuple[Path, Path]:
    """A hook that writes down the payload it was handed, and optionally answers."""
    seen = tmp_path / "seen.json"
    script = tmp_path / "record_hook.sh"
    body = f"#!/bin/sh\ncat > '{seen}'\n"
    if answers:
        body += f"cat <<'HOOK_ANSWER'\n{answers}\nHOOK_ANSWER\n"
    script.write_text(body)
    script.chmod(0o755)
    return seen, script


def _manifest(cache_dir: Path, script: Path, matcher: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "hooks.json").write_text(
        json.dumps({"PreToolUse": [{"matcher": matcher, "command": str(script), "tag": "t"}]})
    )


def _session(tmp_path: Path, monkeypatch, sid: str) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    cache_dir = tmp_path / "cache" / sid
    _in_session(monkeypatch, sid, project)
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("AGY_TOOL_NAME", raising=False)
    return cache_dir


def test_a_bash_row_fires_on_agys_terminal_tool(tmp_path: Path, monkeypatch) -> None:
    """The measured hole: every shipped `Bash` row was compared literally against
    `run_command`, so the shared-state guard never ran on this surface once."""
    cache_dir = _session(tmp_path, monkeypatch, "sid-bash")
    seen, script = _recording_hook(tmp_path)
    _manifest(cache_dir, script, "Bash")
    payload = _agy_payload()

    # Named the way the surface names it — the comparison that used to be literal.
    assert dispatch_hook("PreToolUse", tool_name="run_command", stdin_data=payload) == 0
    assert seen.is_file(), "the guard was installed and never invoked"


def test_the_hook_is_handed_the_claude_dialect(tmp_path: Path, monkeypatch) -> None:
    """Translated once, here — not by four hand-rolled fallbacks and two scripts
    that never learned to."""
    cache_dir = _session(tmp_path, monkeypatch, "sid-dialect")
    seen, script = _recording_hook(tmp_path)
    _manifest(cache_dir, script, "Bash")
    dispatch_hook("PreToolUse", stdin_data=_agy_payload(command="rm -rf /"))

    handed = json.loads(seen.read_text())
    assert handed["tool_input"]["command"] == "rm -rf /"
    assert handed["tool_name"] == "Bash"


def test_the_tool_name_is_taken_from_the_payload_when_argv_is_silent(
    tmp_path: Path, monkeypatch
) -> None:
    """A row for another class must NOT fire. Before this the filter read the
    name from argv alone, and a silent argv switched matching off entirely —
    every hook then ran on every call, edit gates on shell commands included."""
    cache_dir = _session(tmp_path, monkeypatch, "sid-argv")
    seen, script = _recording_hook(tmp_path)
    _manifest(cache_dir, script, "Edit|Write|MultiEdit")
    dispatch_hook("PreToolUse", stdin_data=_agy_payload())

    assert not seen.exists(), "an edit-tool row fired on a terminal call"


def test_a_rewritten_input_leaves_in_the_key_agy_speaks(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The consent ticket (HATS-1642). Answered under `command`, agy finds no
    rewrite and runs the original line — a gate that asks and is then ignored."""
    cache_dir = _session(tmp_path, monkeypatch, "sid-ticket")
    ticket = "AI_HATS_CONSENT" + "_TICKET=t git push --force"
    answer = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "updatedInput": {"command": ticket},
            }
        }
    )
    _seen, script = _recording_hook(tmp_path, answers=answer)
    _manifest(cache_dir, script, "Bash")
    dispatch_hook("PreToolUse", stdin_data=_agy_payload())

    said = json.loads(capsys.readouterr().out)
    assert said["hookSpecificOutput"]["updatedInput"] == {"CommandLine": ticket}
