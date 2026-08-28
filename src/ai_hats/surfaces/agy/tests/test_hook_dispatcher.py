"""Tests for ai-hats-hook-dispatcher in AGY surface plugin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.surfaces.agy.hook_dispatcher import dispatch_hook
from ai_hats.surfaces.hook_channel import HOOK_TIMEOUT_S, resolve_hook_timeout


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


def _said(capsys) -> dict:
    """The verdict agy acts on, off stdout."""
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else {}


def test_a_reclaimed_cache_dir_refuses_rather_than_passing_the_call(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Pin set + no manifest is a reclaimed cache dir, not a hook-less session.

    HATS-1339 chose to report and keep going here; HATS-1439 is what that cost —
    a live session ran with every gate off and one stderr line to show for it.
    The choice is reversed: this refuses like every other surface, and
    ``AI_HATS_GATE_BROKEN_ACK`` is the way past that a human can actually use.
    """
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = tmp_path / "cache"

    _in_session(monkeypatch, "sid-test", project)
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("AI_HATS_GATE_BROKEN_ACK", raising=False)

    dispatch_hook("PreToolUse")
    spoken = _said(capsys)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "no hooks manifest at" in spoken["permissionDecisionReason"]
    assert "AI_HATS_GATE_BROKEN_ACK" in spoken["permissionDecisionReason"]


def test_the_hatch_lets_a_human_past_the_reclaimed_dir(tmp_path: Path, monkeypatch, capsys) -> None:
    """The refusal above is only defensible because this one passes."""
    project = tmp_path / "project"
    project.mkdir()
    _in_session(monkeypatch, "sid-hatch", project)
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AI_HATS_GATE_BROKEN_ACK", "1")

    assert dispatch_hook("PreToolUse") == 0
    assert _said(capsys) == {}, "an opened hatch must not still emit a refusal"


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


def test_a_gone_session_manifest_holds_the_users_hooks_back_too(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """They gate a call that is not going to happen.

    The reverse of what this asserted while the dispatcher passed the call
    through: running the user's PreToolUse hooks behind a refusal would fire
    their side effects for a tool call ai-hats has just cancelled.
    """
    home = tmp_path / "home"
    (home / ".gemini" / "config").mkdir(parents=True)
    marker_file, hook_script = _marker_hook(tmp_path)
    (home / ".gemini" / "config" / "hooks.json").write_text(
        json.dumps({"PreToolUse": [{"matcher": "*", "command": str(hook_script)}]})
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AI_HATS_GATE_BROKEN_ACK", raising=False)
    _in_session(monkeypatch, "sid-gone", tmp_path / "project")
    monkeypatch.setenv("AI_HATS_SESSION_CACHE_DIR", str(tmp_path / "reclaimed"))

    dispatch_hook("PreToolUse", tool_name="Edit")
    assert _said(capsys)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not marker_file.exists(), "a user hook ran for a call that was refused"


def test_dispatcher_without_the_pin_refuses_instead_of_exiting_quietly(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An ai-hats session with no pin has unreachable hooks (HATS-1373 class).

    Passing quietly is what "this session has no hooks" looks like, so it hid
    unreachable guards rather than reporting them.
    """
    _in_session(monkeypatch, "sid-stale", tmp_path / "project")
    monkeypatch.delenv("AI_HATS_SESSION_CACHE_DIR", raising=False)
    monkeypatch.delenv("AI_HATS_GATE_BROKEN_ACK", raising=False)

    dispatch_hook("PreToolUse", tool_name="Edit")
    spoken = _said(capsys)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "AI_HATS_SESSION_CACHE_DIR unset" in spoken["permissionDecisionReason"]


@pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "-5", "nonsense60"])
def test_unusable_budget_override_keeps_the_default(monkeypatch, raw: str) -> None:
    """A typo in the override must not disarm the bound (HATS-1598).

    Every value here is one an operator could plausibly export; if any of them
    resolved to 0 or a negative, the hook would run unbounded again — the exact
    defect the budget exists to close, reintroduced through a config channel.
    """
    monkeypatch.setenv("AI_HATS_AGY_HOOK_TIMEOUT_S", raw)

    assert resolve_hook_timeout() == HOOK_TIMEOUT_S


def test_positive_budget_override_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("AI_HATS_AGY_HOOK_TIMEOUT_S", "2.5")

    assert resolve_hook_timeout() == 2.5


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


class TestArrivalsNothingComposedCanBindTo:
    """agy's global hook registers five events; only two are bindable.

    The other three fell through to ``PreToolUse``, and a payload with no tool
    in it makes ``matches()`` run every row — so a notification put the whole
    gate chain through its paces. Codex has carried the guard this mirrors since
    its own dispatcher was written.
    """

    @pytest.mark.parametrize("arrival", ["Stop", "Notification", "PostInvocation"])
    def test_the_composed_chain_stays_out_of_it(
        self, tmp_path: Path, monkeypatch, arrival: str
    ) -> None:
        cache_dir = _session(tmp_path, monkeypatch, f"sid-{arrival.lower()}")
        seen, script = _recording_hook(tmp_path)
        _manifest(cache_dir, script, "Bash")

        assert dispatch_hook(arrival, stdin_data=_agy_payload()) == 0
        assert not seen.exists(), f"{arrival} ran the whole PreToolUse gate chain"

    @pytest.mark.parametrize("arrival", ["Stop", "Notification", "PostInvocation"])
    def test_the_users_own_hook_for_that_arrival_still_runs(
        self, tmp_path: Path, monkeypatch, arrival: str
    ) -> None:
        """Collapsing the arrival also made ``_user_hooks`` read the PreToolUse
        row for it, so the user's own Stop hook stopped running at all."""
        home = tmp_path / "home"
        (home / ".gemini" / "config").mkdir(parents=True)
        user_marker = tmp_path / "user_ran.txt"
        user_script = tmp_path / "user_hook.sh"
        user_script.write_text(f"#!/bin/sh\necho 'USER' > '{user_marker}'\n")
        user_script.chmod(0o755)
        (home / ".gemini" / "config" / "hooks.json").write_text(
            json.dumps({arrival: [{"matcher": "*", "command": str(user_script)}]})
        )
        monkeypatch.setenv("HOME", str(home))
        cache_dir = _session(tmp_path, monkeypatch, f"sid-user-{arrival.lower()}")
        seen, script = _recording_hook(tmp_path)
        _manifest(cache_dir, script, "Bash")

        assert dispatch_hook(arrival, stdin_data=_agy_payload()) == 0
        assert user_marker.read_text().strip() == "USER"
        assert not seen.exists(), "the composed chain ran on an unbindable arrival"


def test_the_hook_is_told_which_event_it_is_answering(tmp_path: Path, monkeypatch) -> None:
    """cline, codex and the opencode plugin all set it; agy alone did not.

    `pre_bash_shared_state_guard.sh` reads `hook_event_name`, and its absence is
    how that script decides the caller does not speak the protocol — so it took
    the `deny_hard` branch instead of `emit_ask`, giving a hard block with no
    consent path on a surface whose profile declares it can ask with a ticket.
    """
    cache_dir = _session(tmp_path, monkeypatch, "sid-event")
    seen, script = _recording_hook(tmp_path)
    _manifest(cache_dir, script, "Bash")
    # The event on argv and NOWHERE in the payload — which is how agy sends it,
    # and why a fixture that spells hook_event_name itself proves nothing.
    native = json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": "git push"}}})
    dispatch_hook("PreToolUse", stdin_data=native)

    assert json.loads(seen.read_text())["hook_event_name"] == "PreToolUse"
