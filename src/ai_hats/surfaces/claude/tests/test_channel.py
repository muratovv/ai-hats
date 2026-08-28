"""claude's five answers (HATS-1868). The flow itself is ``test_hook_dispatch``.

What is claude-specific and therefore here: which arrival it reads, where its
manifest sits, and the exact JSON the harness acts on. The last one is measured
rather than assumed — ``poc-hook-delivery.md`` ran each shape through a real
``claude -p`` 2.1.247.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from ai_hats.surfaces.claude.channel import ClaudeChannel
from ai_hats.surfaces.claude.profile import PROFILE
from ai_hats.surfaces.hook_channel import (
    GATE_BROKEN_ACK_ENV,
    ChainDecision,
    ChainVerdict,
    HookEvent,
    Nudge,
)
from ai_hats.surfaces.hook_dispatch import Arrival, ManifestUnresolved, dispatch

SESSION = "sid-claude-channel"
_PRE = Arrival.of("PreToolUse")
_POST = Arrival.of("PostToolUse")


def _spoken(capsys) -> dict:
    out = capsys.readouterr().out.strip()
    assert out, "the channel said nothing at all"
    return json.loads(out)


class TestArrival:
    def test_the_event_comes_from_the_payload(self) -> None:
        """The harness names it there and passes no argv to read it from."""
        seen = ClaudeChannel().arrival({"hook_event_name": "PostToolUse"}, ())
        assert (seen.native, seen.event) == ("PostToolUse", HookEvent.POST_TOOL_USE)

    def test_an_arrival_nothing_binds_to_is_reported_as_such(self) -> None:
        assert ClaudeChannel().arrival({"hook_event_name": "SessionStart"}, ()).event is None

    def test_a_payload_with_no_event_binds_to_nothing(self) -> None:
        """Better than defaulting to PreToolUse: a call we cannot name is not a
        call we may guess a gate set for."""
        assert ClaudeChannel().arrival({}, ()).event is None


class TestRead:
    def test_the_payload_travels_untranslated(self) -> None:
        """claude already speaks the vocabulary the hooks are written in, which
        is the whole reason its `tool_names` row is empty."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "true"}}
        (call,) = ClaudeChannel().read(payload, _PRE)
        assert call.payload is payload
        assert call.tool == "Bash"

    def test_one_call_per_payload(self) -> None:
        """cline fans a command list and codex a patch; claude sends one call
        and inventing a second would gate something the model never asked for."""
        assert len(ClaudeChannel().read({"tool_name": "Edit"}, _PRE)) == 1


class TestSpeaks:
    def test_the_dialect_does_not_narrow_by_arrival(self) -> None:
        """codex's does; claude's does not, and saying so beats leaving a reader
        to infer it from an absent branch."""
        assert ClaudeChannel().speaks(_PRE) == PROFILE.speaks
        assert ClaudeChannel().speaks(_POST) == PROFILE.speaks


class TestEmit:
    def test_an_allowing_silent_chain_says_nothing(self, capsys) -> None:
        """Silence is the cheapest correct answer, and the harness reads it as
        an allow."""
        ClaudeChannel().emit(ChainVerdict(decision=ChainDecision.ALLOW), _PRE)
        assert capsys.readouterr().out == ""

    def test_a_refusal_is_the_dialect_the_harness_acts_on(self, capsys) -> None:
        verdict = ChainVerdict(
            decision=ChainDecision.DENY, reason="not on my watch", hook="ai-hats:guard"
        )
        ClaudeChannel().emit(verdict, _PRE)
        spoken = _spoken(capsys)["hookSpecificOutput"]
        assert spoken["hookEventName"] == "PreToolUse"
        assert spoken["permissionDecision"] == "deny"
        assert spoken["permissionDecisionReason"] == "not on my watch"

    def test_a_delivery_refusal_carries_the_way_past_it(self, capsys) -> None:
        """A deny that names a flag nobody can use only teaches the reader to
        disable ai-hats wholesale."""
        verdict = ChainVerdict(
            decision=ChainDecision.DENY, reason="the gate is gone", hatch_env=GATE_BROKEN_ACK_ENV
        )
        ClaudeChannel().emit(verdict, _PRE)
        said = _spoken(capsys)["hookSpecificOutput"]["permissionDecisionReason"]
        assert "the gate is gone" in said
        assert GATE_BROKEN_ACK_ENV in said

    def test_a_refusal_also_reaches_the_session_log(self, capsys) -> None:
        """The model reads the JSON; a human reading the log afterwards reads
        stderr, and in headless there is otherwise nothing there at all."""
        ClaudeChannel().emit(ChainVerdict(decision=ChainDecision.DENY, reason="no"), _PRE)
        assert "no" in capsys.readouterr().err

    def test_a_refusal_after_the_fact_uses_the_other_spelling(self, capsys) -> None:
        """PostToolUse has no `permissionDecision`; this pair is how a refusal
        is spelled for a call that already ran."""
        verdict = ChainVerdict(
            decision=ChainDecision.DENY, reason="that wrote a secret", event=HookEvent.POST_TOOL_USE
        )
        ClaudeChannel().emit(verdict, _POST)
        said = _spoken(capsys)
        assert said == {"decision": "block", "reason": "that wrote a secret"}

    def test_a_question_travels_with_its_ticket(self, capsys) -> None:
        """`safety_gate.py` mints a nonce and rewrites the command with it. A
        question asked without the rewrite approves the ORIGINAL line."""
        verdict = ChainVerdict(
            decision=ChainDecision.ASK,
            reason="confirm this",
            updated_input={"command": "echo REWRITTEN"},
        )
        ClaudeChannel().emit(verdict, _PRE)
        spoken = _spoken(capsys)["hookSpecificOutput"]
        assert spoken["permissionDecision"] == "ask"
        assert spoken["updatedInput"] == {"command": "echo REWRITTEN"}

    def test_advice_reaches_the_model_with_every_author(self, capsys) -> None:
        verdict = ChainVerdict(
            decision=ChainDecision.ALLOW,
            nudges=(Nudge("prefer Grep", "ai-hats:one"), Nudge("mind the budget", "ai-hats:two")),
        )
        ClaudeChannel().emit(verdict, _PRE)
        said = _spoken(capsys)["hookSpecificOutput"]["additionalContext"]
        assert said == "prefer Grep\nmind the budget"

    def test_advice_survives_a_refusal(self, capsys) -> None:
        """The dialect says this surface carries advice; dropping it on a deny
        made that promise false for every gate before the objector."""
        verdict = ChainVerdict(
            decision=ChainDecision.DENY, reason="no", nudges=(Nudge("heads up", "ai-hats:one"),)
        )
        ClaudeChannel().emit(verdict, _PRE)
        spoken = _spoken(capsys)["hookSpecificOutput"]
        assert spoken["permissionDecision"] == "deny"
        assert spoken["additionalContext"] == "heads up"

    def test_the_status_is_zero_because_the_verdict_rides_the_json(self, capsys) -> None:
        """Exit 2 blocks too, but its reason travels on stderr alone — which
        drops the ticket and the advice this dialect can carry."""
        assert (
            ClaudeChannel().emit(ChainVerdict(decision=ChainDecision.DENY, reason="no"), _PRE) == 0
        )


class TestRows:
    def _session(self, tmp_path: Path, *, hooks: dict | None = None) -> tuple[Path, dict]:
        cache = tmp_path / "cache"
        mirror = cache / "plugin" / "skills" / "safety-guard"
        mirror.mkdir(parents=True)
        gate = mirror / "gate.sh"
        gate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gate.chmod(0o755)
        rows = [{"command": str(gate), "matcher": "Bash", "tag": "ai-hats:safety-guard"}]
        (cache / "hooks.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "session": {"id": SESSION},
                    "hooks": hooks if hooks is not None else {"PreToolUse": rows},
                }
            ),
            encoding="utf-8",
        )
        return gate, {
            "AI_HATS_SESSION_ID": SESSION,
            "AI_HATS_SESSION_CACHE_DIR": str(cache),
        }

    def test_the_manifest_under_the_session_cache_is_what_is_read(self, tmp_path: Path) -> None:
        gate, env = self._session(tmp_path)
        (row,) = ClaudeChannel().rows(env, HookEvent.PRE_TOOL_USE)
        assert row.command == gate.resolve()
        assert row.tag == "ai-hats:safety-guard"

    def test_a_command_outside_the_plugin_mirror_is_refused(self, tmp_path: Path) -> None:
        """The mirror is `<cache>/plugin/skills`, and it is a profile row rather
        than a path spelled again here."""
        cache = tmp_path / "cache"
        cache.mkdir()
        stray = tmp_path / "stray.sh"
        stray.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stray.chmod(0o755)
        (cache / "hooks.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "session": {"id": SESSION},
                    "hooks": {"PreToolUse": [{"command": str(stray)}]},
                }
            ),
            encoding="utf-8",
        )
        env = {"AI_HATS_SESSION_ID": SESSION, "AI_HATS_SESSION_CACHE_DIR": str(cache)}
        with pytest.raises(ManifestUnresolved, match="escapes the session skills mirror"):
            ClaudeChannel().rows(env, HookEvent.PRE_TOOL_USE)

    @pytest.mark.parametrize("missing", ["AI_HATS_SESSION_ID", "AI_HATS_SESSION_CACHE_DIR"])
    def test_an_incomplete_identity_refuses_rather_than_guessing(
        self, tmp_path: Path, missing: str
    ) -> None:
        """Neither pin can be derived. Guessing a cache dir is how a session
        would run against another one's gate set."""
        _, env = self._session(tmp_path)
        env.pop(missing)
        with pytest.raises(ManifestUnresolved, match="incomplete hook identity"):
            ClaudeChannel().rows(env, HookEvent.PRE_TOOL_USE)


class TestEndToEndThroughDispatch:
    """The five answers wired to the flow, so a break in either shows up here."""

    def test_a_gate_that_vanished_refuses_the_call(self, tmp_path: Path, capsys) -> None:
        """M4 inverted. The harness passes this call with rc=0 and zero bytes on
        stderr; the whole card is about it refusing instead."""
        cache = tmp_path / "cache"
        (cache / "plugin" / "skills").mkdir(parents=True)
        (cache / "hooks.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "session": {"id": SESSION},
                    "hooks": {
                        "PreToolUse": [
                            {
                                "command": str(cache / "plugin" / "skills" / "GONE.py"),
                                "matcher": "Bash",
                                "tag": "ai-hats:vanished",
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
        code = dispatch(
            ClaudeChannel(),
            stdin=io.StringIO(json.dumps(payload)),
            environ={
                "AI_HATS_SESSION_ID": SESSION,
                "AI_HATS_SESSION_CACHE_DIR": str(cache),
                "AI_HATS_PROJECT_DIR": str(tmp_path),
            },
        )
        assert code == 0
        spoken = _spoken(capsys)["hookSpecificOutput"]
        assert spoken["permissionDecision"] == "deny"
        assert GATE_BROKEN_ACK_ENV in spoken["permissionDecisionReason"]

    def test_the_control_a_live_gate_lets_the_call_through(self, tmp_path: Path, capsys) -> None:
        """Without this, the refusal above is indistinguishable from a channel
        that refuses everything it is handed."""
        cache = tmp_path / "cache"
        mirror = cache / "plugin" / "skills"
        mirror.mkdir(parents=True)
        gate = mirror / "gate.sh"
        gate.write_text("#!/bin/sh\ncat >/dev/null\nexit 0\n", encoding="utf-8")
        gate.chmod(0o755)
        (cache / "hooks.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "session": {"id": SESSION},
                    "hooks": {
                        "PreToolUse": [
                            {"command": str(gate), "matcher": "Bash", "tag": "ai-hats:live"}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
        code = dispatch(
            ClaudeChannel(),
            stdin=io.StringIO(json.dumps(payload)),
            environ={
                "AI_HATS_SESSION_ID": SESSION,
                "AI_HATS_SESSION_CACHE_DIR": str(cache),
                "AI_HATS_PROJECT_DIR": str(tmp_path),
            },
        )
        assert code == 0
        assert capsys.readouterr().out == "", "an allowing chain has nothing to say"
