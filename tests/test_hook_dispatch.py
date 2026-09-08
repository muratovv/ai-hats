"""``dispatch`` — the flow four dispatchers used to write by hand (HATS-1868).

Driven through a stand-in channel rather than a real surface: the contract here
is the ORDER and the completeness of the steps, and a real surface would prove
its own emit instead. The surface half is
``src/ai_hats/surfaces/claude/tests/test_channel.py``.

Each test names the drift it refuses. They are not hypothetical — HATS-1858's
review found four different behaviours for "the manifest is gone", one per
hand-written copy, and after the fix the reduction still lived in three places.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_hats.surfaces import profiles
from ai_hats.surfaces.hook_channel import (
    GATE_BROKEN_ACK_ENV,
    ChainDecision,
    ChainVerdict,
    Dialect,
    HookCall,
    HookEvent,
    HookRow,
    Nudge,
)
from ai_hats.surfaces.hook_dispatch import (
    Arrival,
    ManifestUnresolved,
    dispatch,
    manifest_rows,
)

_PAYLOAD = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "true"}}


class Recorder:
    """A surface that answers the five questions and records what it was asked."""

    profile = profiles.CLAUDE

    def __init__(self, *, rows=(), raises: str = "", speaks: Dialect | None = None):
        self._rows = list(rows)
        self._raises = raises
        # The dialect is a ROW, not an answer — a channel under test narrows it
        # the way a real surface does, by carrying a profile that says so.
        self.profile = (
            profiles.CLAUDE if speaks is None else replace(profiles.CLAUDE, speaks=speaks)
        )
        self.said: list[ChainVerdict] = []
        self.arrivals: list[Arrival] = []
        self.rows_asked = 0

    def arrival(self, payload, argv):
        seen = Arrival.of(str(payload.get("hook_event_name", "")) or (argv[0] if argv else ""))
        self.arrivals.append(seen)
        return seen

    def rows(self, environ, event):
        self.rows_asked += 1
        if self._raises:
            raise ManifestUnresolved(self._raises)
        return self._rows

    def read(self, payload, arrival):
        return [HookCall(payload, str(payload.get("tool_name", "")))]

    def emit(self, verdict, arrival):
        self.said.append(verdict)


def _hook(tmp_path: Path, name: str, body: str) -> HookRow:
    script = tmp_path / name
    script.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return HookRow(command=script, matcher="Bash", tag=f"ai-hats:{name}")


@pytest.fixture
def _run(hook_repo):
    def run(channel, payload=_PAYLOAD, *, argv=(), environ=None) -> int:
        body = payload if isinstance(payload, str) else json.dumps(payload)
        return dispatch(
            channel,
            stdin=io.StringIO(body),
            argv=argv,
            environ={**(environ or {}), "AI_HATS_PROJECT_DIR": str(hook_repo)},
        )

    return run


class TestEveryPathReachesTheSurfaceThroughOneAnswer:
    """The drift HATS-1858 found: a delivery refusal that skipped the reduction
    on two surfaces of three and cancelled what cannot be cancelled."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ("{not json", "invalid payload"),
            ('["a list"]', "payload is list, not an object"),
        ],
    )
    def test_an_unreadable_payload_refuses_and_names_the_hatch(
        self, _run, payload, expected
    ) -> None:
        channel = Recorder()
        _run(channel, payload)
        (verdict,) = channel.said
        assert verdict.decision is ChainDecision.DENY
        assert expected in verdict.reason
        assert verdict.hatch_env == GATE_BROKEN_ACK_ENV
        assert channel.rows_asked == 0, "the manifest was read for a call we could not parse"

    def test_a_manifest_that_never_resolved_refuses_and_names_the_hatch(self, _run) -> None:
        channel = Recorder(raises="cannot read session hook manifest /gone/hooks.json")
        _run(channel)
        (verdict,) = channel.said
        assert verdict.decision is ChainDecision.DENY
        assert "/gone/hooks.json" in verdict.reason
        assert verdict.hatch_env == GATE_BROKEN_ACK_ENV

    def test_the_refusal_is_worded_in_this_surfaces_own_name(self, _run) -> None:
        """An operator reading a session log has to know which channel spoke."""
        channel = Recorder(raises="manifest gone")
        _run(channel)
        assert "ai-hats-claude-hook:" in channel.said[0].reason

    def test_a_delivery_refusal_is_reduced_like_any_other_verdict(self, _run) -> None:
        """The drift itself: on a surface that cannot deny after the fact, a
        refusal for a call that ALREADY ran must become something the reader
        sees — not a cancellation of what cannot be cancelled."""
        mute = Dialect(
            can_ask=False, can_ask_with_ticket=False, can_deny_after=False, can_carry_nudges=True
        )
        channel = Recorder(raises="manifest gone", speaks=mute)
        _run(channel, {**_PAYLOAD, "hook_event_name": "PostToolUse"})
        (verdict,) = channel.said
        assert verdict.decision is ChainDecision.ALLOW
        assert any("manifest gone" in n.text for n in verdict.nudges), verdict
        assert any(GATE_BROKEN_ACK_ENV in n.text for n in verdict.nudges), (
            "the way past went missing with the refusal it belonged to"
        )

    def test_the_hatch_opens_a_delivery_refusal_here_too(self, _run) -> None:
        channel = Recorder(raises="manifest gone")
        _run(channel, environ={GATE_BROKEN_ACK_ENV: "1"})
        assert channel.said[0].decision is ChainDecision.ALLOW


class TestAnArrivalNothingBindsTo:
    def test_an_unknown_arrival_allows_without_reading_the_manifest(self, _run) -> None:
        """Nothing composed can bind there, so no gate was missed — and reading
        the manifest for it is how agy came to run every gate on a Notification."""
        channel = Recorder()
        _run(channel, {**_PAYLOAD, "hook_event_name": "Notification"})
        assert channel.said[0].decision is ChainDecision.ALLOW
        assert channel.rows_asked == 0

    def test_an_arrival_outside_the_profiles_row_allows_too(self, _run) -> None:
        """`native_events` is the row that says what a surface delivers; an
        arrival missing from it is not this channel's call to judge."""
        channel = Recorder()
        channel.profile = replace(profiles.CLAUDE, native_events=("PostToolUse",))
        _run(channel)
        assert channel.said[0].decision is ChainDecision.ALLOW
        assert channel.rows_asked == 0


class TestTheChainIsActuallyRun:
    def test_a_refusing_gate_reaches_the_surface_with_its_reason(
        self, _run, tmp_path: Path
    ) -> None:
        doc = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "not on my watch",
                }
            }
        )
        row = _hook(tmp_path, "deny", f"cat >/dev/null; printf '%s' {json.dumps(doc)}")
        channel = Recorder(rows=[row])
        _run(channel)
        (verdict,) = channel.said
        assert (verdict.decision, verdict.reason) == (ChainDecision.DENY, "not on my watch")
        assert verdict.hatch_env == "", "a gate's own refusal carries no hatch of ours"

    def test_a_silent_chain_allows(self, _run, tmp_path: Path) -> None:
        row = _hook(tmp_path, "quiet", "cat >/dev/null; exit 0")
        channel = Recorder(rows=[row])
        _run(channel)
        assert channel.said[0].decision is ChainDecision.ALLOW

    def test_the_gate_is_handed_the_payload_this_channel_read(self, _run, tmp_path: Path) -> None:
        seen = tmp_path / "seen.json"
        row = _hook(tmp_path, "echo", f"cat > {seen}")
        _run(Recorder(rows=[row]))
        assert json.loads(seen.read_text(encoding="utf-8"))["tool_name"] == "Bash"

    def test_a_row_whose_matcher_misses_never_runs(self, _run, tmp_path: Path) -> None:
        ran = tmp_path / "ran"
        row = _hook(tmp_path, "edits", f"cat >/dev/null; touch {ran}")
        _run(Recorder(rows=[replace(row, matcher="Edit|Write")]))
        assert not ran.exists()


class TestTheDialectIsARow:
    """It is known before any hook arrives, so it is read rather than asked for.

    codex narrows `can_ask` to one arrival and wrote that as a function of the
    event; as a row it cannot disagree with the profile that declares it.
    """

    def test_the_narrowing_for_this_arrival_is_honoured(self, _run, tmp_path: Path) -> None:
        doc = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "may I",
                }
            }
        )
        row = _hook(tmp_path, "ask", f"cat >/dev/null; printf '%s' {json.dumps(doc)}")
        channel = Recorder(rows=[row])
        channel.profile = replace(
            profiles.CLAUDE,
            speaks_on={"PreToolUse": replace(profiles.CLAUDE.speaks, can_ask=False)},
        )
        _run(channel)
        assert channel.said[0].decision is ChainDecision.DENY

    def test_the_control_another_arrival_keeps_the_surfaces_own_dialect(self) -> None:
        """A narrowing that leaked onto every arrival would be invisible above."""
        narrowed = replace(
            profiles.CLAUDE,
            speaks_on={"PermissionRequest": replace(profiles.CLAUDE.speaks, can_ask=False)},
        )
        assert narrowed.dialect("PreToolUse").can_ask
        assert not narrowed.dialect("PermissionRequest").can_ask

    def test_a_narrower_dialect_on_this_arrival_is_honoured(self, _run, tmp_path: Path) -> None:
        doc = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "may I",
                }
            }
        )
        row = _hook(tmp_path, "ask", f"cat >/dev/null; printf '%s' {json.dumps(doc)}")
        cannot_ask = replace(profiles.CLAUDE.speaks, can_ask=False)
        channel = Recorder(rows=[row], speaks=cannot_ask)
        _run(channel)
        (verdict,) = channel.said
        assert verdict.decision is ChainDecision.DENY, (
            "a question this arrival cannot put must become a refusal that says why"
        )
        assert "may I" in verdict.reason and "cannot carry the consent" in verdict.reason

    def test_the_control_the_same_question_survives_where_the_arrival_can_ask(
        self, _run, tmp_path: Path
    ) -> None:
        """Without this, the refusal above is indistinguishable from a channel
        that turns every `ask` into a deny."""
        doc = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "may I",
                }
            }
        )
        row = _hook(tmp_path, "ask", f"cat >/dev/null; printf '%s' {json.dumps(doc)}")
        channel = Recorder(rows=[row])
        _run(channel)
        assert channel.said[0].decision is ChainDecision.ASK


class TestManifestRows:
    """Checks three dispatchers wrote for themselves, and opencode wrote none of
    the last two — it executed whatever string the manifest named."""

    def _write(self, cache: Path, hooks: dict, *, session: str = "sid", version: int = 1) -> Path:
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / "hooks.json"
        path.write_text(
            json.dumps({"version": version, "session": {"id": session}, "hooks": hooks}),
            encoding="utf-8",
        )
        return path

    def _mirror(self, tmp_path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
        root = tmp_path / "mirror"
        root.mkdir(parents=True, exist_ok=True)
        script = root / "gate.sh"
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
        return script

    def _rows(self, path: Path, root: Path, session: str = "sid"):
        return manifest_rows(
            path, event=HookEvent.PRE_TOOL_USE, session_id=session, skills_root=root.resolve()
        )

    def test_a_well_formed_manifest_yields_its_rows(self, tmp_path: Path) -> None:
        script = self._mirror(tmp_path)
        path = self._write(
            tmp_path / "cache",
            {"PreToolUse": [{"command": str(script), "matcher": "Bash", "tag": "ai-hats:g"}]},
        )
        (row,) = self._rows(path, script.parent)
        assert (row.command, row.matcher, row.tag) == (script.resolve(), "Bash", "ai-hats:g")

    @pytest.mark.parametrize(
        ("hooks", "expected"),
        [
            ({"PreToolUse": [{"matcher": "Bash"}]}, "malformed hook entry"),
            ({"PreToolUse": ["a string"]}, "hook entry is str, not an object"),
            ({"PreToolUse": [{"command": ""}]}, "malformed hook entry"),
        ],
    )
    def test_a_row_it_cannot_read_refuses_rather_than_being_skipped(
        self, tmp_path: Path, hooks, expected
    ) -> None:
        """Dropping it silently is indistinguishable from never declaring it."""
        script = self._mirror(tmp_path)
        path = self._write(tmp_path / "cache", hooks)
        with pytest.raises(ManifestUnresolved, match=expected):
            self._rows(path, script.parent)

    def test_a_command_outside_the_mirror_is_refused(self, tmp_path: Path) -> None:
        script = self._mirror(tmp_path)
        stray = tmp_path / "stray.sh"
        stray.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stray.chmod(0o755)
        path = self._write(tmp_path / "cache", {"PreToolUse": [{"command": str(stray)}]})
        with pytest.raises(ManifestUnresolved, match="escapes the session skills mirror"):
            self._rows(path, script.parent)

    def test_a_command_without_the_executable_bit_is_refused(self, tmp_path: Path) -> None:
        """Removing +x must not become a quiet way to disarm a gate."""
        script = self._mirror(tmp_path)
        script.chmod(0o644)
        path = self._write(tmp_path / "cache", {"PreToolUse": [{"command": str(script)}]})
        with pytest.raises(ManifestUnresolved, match="not an executable session file"):
            self._rows(path, script.parent)

    def test_a_manifest_from_another_session_is_refused(self, tmp_path: Path) -> None:
        script = self._mirror(tmp_path)
        path = self._write(
            tmp_path / "cache", {"PreToolUse": [{"command": str(script)}]}, session="someone-else"
        )
        with pytest.raises(ManifestUnresolved, match="belongs to another session"):
            self._rows(path, script.parent)

    def test_an_unknown_version_is_refused(self, tmp_path: Path) -> None:
        script = self._mirror(tmp_path)
        path = self._write(
            tmp_path / "cache", {"PreToolUse": [{"command": str(script)}]}, version=99
        )
        with pytest.raises(ManifestUnresolved, match="unsupported hook manifest"):
            self._rows(path, script.parent)

    def test_a_missing_manifest_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestUnresolved, match="cannot read session hook manifest"):
            self._rows(tmp_path / "nowhere.json", tmp_path)

    def test_an_event_with_no_rows_is_not_an_error(self, tmp_path: Path) -> None:
        """No gate bound to this event is a fact, not a failure."""
        path = self._write(tmp_path / "cache", {"PostToolUse": []})
        assert self._rows(path, tmp_path) == []


class TestTheStatusIsDerivedFromTheVerdict:
    """Not chosen while writing the reply: the document is the answer on every
    surface, and a status is a projection two of them additionally act on."""

    def test_an_allow_exits_zero(self, _run) -> None:
        assert _run(Recorder()) == 0

    def test_a_refusal_a_hook_uttered_exits_zero_on_a_surface_that_reads_none(
        self, _run, tmp_path: Path
    ) -> None:
        doc = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "no",
                }
            }
        )
        row = _hook(tmp_path, "deny", f"cat >/dev/null; printf '%s' {json.dumps(doc)}")
        assert _run(Recorder(rows=[row])) == 0

    def test_a_refusal_ai_hats_imposed_carries_the_surfaces_own_status(self, _run) -> None:
        channel = Recorder(raises="manifest gone")
        channel.profile = replace(profiles.CLAUDE, imposed_status=2)
        assert _run(channel) == 2

    def test_a_hooks_own_refusal_never_borrows_that_status(self, _run, tmp_path: Path) -> None:
        """Arguing with a gate that RAN is between its author and whoever it
        stopped; inventing a status for it would overrule them."""
        doc = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "no",
                }
            }
        )
        row = _hook(tmp_path, "deny", f"cat >/dev/null; printf '%s' {json.dumps(doc)}")
        channel = Recorder(rows=[row])
        channel.profile = replace(profiles.CLAUDE, imposed_status=2)
        assert _run(channel) == 0

    def test_a_surface_whose_protocol_is_the_status_forwards_the_childs(
        self, _run, tmp_path: Path
    ) -> None:
        """agy's BROKE contract (HATS-1598): exit 2 from a gate travels out."""
        row = _hook(tmp_path, "hard", "cat >/dev/null; echo 'BLOCKED' >&2; exit 2")
        channel = Recorder(rows=[row])
        channel.profile = replace(profiles.CLAUDE, forwards_hook_status=True)
        assert _run(channel) == 2


def test_the_arrival_helper_maps_only_what_binds() -> None:
    assert Arrival.of("PreToolUse").event is HookEvent.PRE_TOOL_USE
    assert Arrival.of("Notification").event is None
    assert Arrival.of("Notification").native == "Notification"


def test_a_nudge_keeps_its_author_through_the_flow(_run, tmp_path: Path) -> None:
    doc = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "prefer Grep"}}
    )
    row = _hook(tmp_path, "nudge", f"cat >/dev/null; printf '%s' {json.dumps(doc)}")
    channel = Recorder(rows=[row])
    _run(channel)
    assert channel.said[0].nudges == (Nudge("prefer Grep", "ai-hats:nudge"),)
