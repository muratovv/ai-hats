"""The shared channel: how one hook's answer is read, and what a surface may say."""

from __future__ import annotations

import json

import pytest

from ai_hats.libraries.models import RUNTIME_HOOK_EVENTS
from ai_hats.surfaces.hook_channel import (
    ChainDecision,
    ChainVerdict,
    Dialect,
    HookEvent,
    Nudge,
    ReplyUnreadable,
    parse_reply,
    reduce_to,
)


def _spoken(**hook_specific) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **hook_specific}})


class TestHookEvent:
    def test_it_holds_exactly_the_events_a_hook_may_be_bound_to(self) -> None:
        """Drift here would let a bindable event have no member to parse into."""
        assert {e.value for e in HookEvent} == set(RUNTIME_HOOK_EVENTS)

    def test_a_name_no_hook_can_be_bound_to_parses_to_nothing(self) -> None:
        assert HookEvent.parse("PermissionRequest") is None
        assert HookEvent.parse("") is None


class TestParseReply:
    def test_silence_names_no_decision(self) -> None:
        """Nothing said still lets the call through — but says nobody looked."""
        reply = parse_reply("", exit_code=0)
        assert reply.decision is None
        assert reply.nudge is None

    def test_a_gate_that_looked_and_allowed_is_not_silence(self) -> None:
        reply = parse_reply(_spoken(permissionDecision="allow"), exit_code=0)
        assert reply.decision is ChainDecision.ALLOW

    def test_exit_two_refuses_and_its_reason_comes_off_stderr(self) -> None:
        """The one shipped exit-2 hook writes its case nowhere else."""
        reply = parse_reply("", exit_code=2, stderr="  BLOCKED: shared state  ")
        assert reply.decision is ChainDecision.DENY
        assert reply.reason == "BLOCKED: shared state"

    def test_a_refusal_with_no_stderr_still_states_a_case(self) -> None:
        assert parse_reply("", exit_code=2).reason

    def test_a_denial_carries_its_reason(self) -> None:
        reply = parse_reply(
            _spoken(permissionDecision="deny", permissionDecisionReason="off limits"),
            exit_code=0,
        )
        assert (reply.decision, reply.reason) == (ChainDecision.DENY, "off limits")

    def test_a_top_level_block_outranks_the_permission_field(self) -> None:
        raw = json.loads(_spoken(permissionDecision="allow"))
        raw.update({"decision": "block", "reason": "after the fact"})
        reply = parse_reply(json.dumps(raw), exit_code=0)
        assert (reply.decision, reply.reason) == (ChainDecision.DENY, "after the fact")

    def test_an_ask_keeps_the_rewrite_that_travelled_with_it(self) -> None:
        """Consent is one reply: the question and the ticketed command together."""
        reply = parse_reply(
            _spoken(
                permissionDecision="ask",
                permissionDecisionReason="needs consent",
                updatedInput={"command": "TICKET=1 git push"},
            ),
            exit_code=0,
        )
        assert reply.decision is ChainDecision.ASK
        assert reply.updated_input == {"command": "TICKET=1 git push"}

    def test_a_nudge_names_the_hook_that_wrote_it(self) -> None:
        reply = parse_reply(
            _spoken(additionalContext="prefer Grep"), exit_code=0, hook="ai-hats:hygiene"
        )
        assert reply.nudge == Nudge("prefer Grep", "ai-hats:hygiene")

    def test_the_event_the_hook_answered_under_survives(self) -> None:
        raw = json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse"}})
        assert parse_reply(raw, exit_code=0).event is HookEvent.POST_TOOL_USE

    @pytest.mark.parametrize("body", ["{not json", '"a string"', "[1, 2]"])
    def test_an_unreadable_answer_is_never_mistaken_for_silence(self, body: str) -> None:
        with pytest.raises(ReplyUnreadable):
            parse_reply(body, exit_code=0)

    def test_a_truncated_answer_is_unreadable_even_when_it_still_parses(self) -> None:
        """A tail cuts the document's head, so what survives is not the verdict."""
        with pytest.raises(ReplyUnreadable):
            parse_reply(_spoken(permissionDecision="deny"), exit_code=0, truncated=True)

    def test_a_truncated_answer_that_kept_nothing_is_unreadable_too(self) -> None:
        with pytest.raises(ReplyUnreadable):
            parse_reply("", exit_code=0, truncated=True)


_MUTE = Dialect(can_ask_with_ticket=False, can_deny_after=False, can_carry_nudges=False)
_FLUENT = Dialect(can_ask_with_ticket=True, can_deny_after=True, can_carry_nudges=True)


class TestReduceTo:
    def test_a_surface_that_cannot_ask_refuses_instead(self) -> None:
        asked = ChainVerdict(decision=ChainDecision.ASK, reason="needs consent")
        assert reduce_to(_MUTE, asked).decision is ChainDecision.DENY

    def test_the_refusal_drops_the_rewrite_the_question_carried(self) -> None:
        """Approving the ticketed command is the question; without it, nothing
        approved that input."""
        asked = ChainVerdict(decision=ChainDecision.ASK, updated_input={"command": "x"})
        assert reduce_to(_MUTE, asked).updated_input is None

    def test_every_reduction_applies_not_just_the_first(self) -> None:
        """A surface that can neither ask nor carry a nudge needs both."""
        asked = ChainVerdict(decision=ChainDecision.ASK, nudges=(Nudge("hint"),))
        reduced = reduce_to(_MUTE, asked)
        assert (reduced.decision, reduced.nudges) == (ChainDecision.DENY, ())

    def test_a_fluent_surface_is_handed_the_verdict_untouched(self) -> None:
        asked = ChainVerdict(
            decision=ChainDecision.ASK, nudges=(Nudge("hint"),), updated_input={"command": "x"}
        )
        assert reduce_to(_FLUENT, asked) == asked

    def test_a_refusal_is_never_softened(self) -> None:
        denied = ChainVerdict(decision=ChainDecision.DENY, reason="off limits")
        assert reduce_to(_MUTE, denied).decision is ChainDecision.DENY


def test_the_name_a_payload_carries_is_one_its_matcher_accepts() -> None:
    """Otherwise a hook is handed a call its own matcher would have rejected."""
    from ai_hats.surfaces import profiles

    for profile in profiles.ALL:
        for native in profile.tool_names:
            spoken = profile.spoken_name(native)
            assert spoken in profile.matcher_names(native), f"{profile.label}: {native}"
