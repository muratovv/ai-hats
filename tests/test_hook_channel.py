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


_MUTE = Dialect(
    can_ask=False, can_ask_with_ticket=False, can_deny_after=False, can_carry_nudges=False
)
_FLUENT = Dialect(
    can_ask=True, can_ask_with_ticket=True, can_deny_after=True, can_carry_nudges=True
)
#: A prompt, but no way to carry a rewrite with it — codex's shape.
_PROMPT_ONLY = Dialect(
    can_ask=True, can_ask_with_ticket=False, can_deny_after=True, can_carry_nudges=True
)


class TestReduceTo:
    def test_a_surface_that_cannot_ask_refuses_instead(self) -> None:
        asked = ChainVerdict(decision=ChainDecision.ASK, reason="needs consent")
        assert reduce_to(_MUTE, asked).decision is ChainDecision.DENY

    def test_a_question_with_nothing_to_lose_survives_where_a_prompt_exists(self) -> None:
        """Deferring to the surface's own prompt drops nothing when no ticket
        travelled with the question."""
        asked = ChainVerdict(decision=ChainDecision.ASK, reason="needs consent")
        assert reduce_to(_PROMPT_ONLY, asked).decision is ChainDecision.ASK

    def test_a_question_carrying_a_ticket_refuses_where_the_ticket_cannot_follow(self) -> None:
        """The prompt would show the ORIGINAL command and approve that instead."""
        asked = ChainVerdict(decision=ChainDecision.ASK, updated_input={"command": "TICKET=1 x"})
        assert reduce_to(_PROMPT_ONLY, asked).decision is ChainDecision.DENY

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


class TestArgumentNames:
    def test_a_surface_key_becomes_the_one_hooks_defend_against(self) -> None:
        from ai_hats.surfaces import profiles
        from ai_hats.surfaces.hook_channel import speak_args

        spoken = speak_args(profiles.AGY, {"CommandLine": "git push"})
        assert spoken == {"command": "git push"}

    def test_a_key_the_payload_already_spelled_is_left_alone(self) -> None:
        """The payload said it; the surface's guess must not overwrite its word."""
        from ai_hats.surfaces import profiles
        from ai_hats.surfaces.hook_channel import speak_args

        spoken = speak_args(profiles.AGY, {"CommandLine": "guess", "command": "said"})
        assert spoken["command"] == "said"

    def test_the_way_back_names_the_key_this_payload_used(self) -> None:
        """A rewrite returned under the wrong key runs the original line."""
        from ai_hats.surfaces import profiles
        from ai_hats.surfaces.hook_channel import native_arg_keys

        assert native_arg_keys(profiles.AGY, {"TargetFile": "/x"}) == {"file_path": "TargetFile"}

    def test_a_surface_with_no_renames_changes_nothing(self) -> None:
        from ai_hats.surfaces import profiles
        from ai_hats.surfaces.hook_channel import native_arg_keys, speak_args

        assert speak_args(profiles.CODEX, {"command": "x"}) == {"command": "x"}
        assert native_arg_keys(profiles.CODEX, {"command": "x"}) == {}


def test_an_uncompilable_matcher_still_guards_the_tools_it_spells() -> None:
    """`Edit|Write|[` compiles nowhere, and it still means to guard Edit."""
    from ai_hats.surfaces import profiles
    from ai_hats.surfaces.hook_channel import matches

    assert matches(profiles.CLINE, "Edit|Write|[", "replace_in_file")
    assert not matches(profiles.CLINE, "Edit|Write|[", "read_file")


def test_a_call_we_cannot_name_meets_every_gate_rather_than_none() -> None:
    """Filtering on a name we do not have would drop every gate for exactly the
    call we understand least. Each hook decides for itself instead."""
    from ai_hats.surfaces import profiles
    from ai_hats.surfaces.hook_channel import matches

    assert matches(profiles.AGY, "Edit|Write|MultiEdit", "")
    assert matches(profiles.AGY, "Bash", "")


class TestEventCoverage:
    """A new bindable event must not be droppable by forgetting a line."""

    def test_every_surface_delivers_every_bindable_event(self) -> None:
        from ai_hats.surfaces import profiles
        from ai_hats.surfaces.hook_channel import BINDABLE_EVENTS

        for profile in profiles.ALL:
            missing = set(BINDABLE_EVENTS) - set(profile.native_events)
            assert not missing, (
                f"{profile.label} would never receive {sorted(missing)} — a hook bound "
                f"there composes fine and never fires"
            )

    def test_the_bindable_set_is_the_enum_and_nothing_else(self) -> None:
        from ai_hats.surfaces.hook_channel import BINDABLE_EVENTS, HookEvent

        assert BINDABLE_EVENTS == tuple(e.value for e in HookEvent)

    def test_a_surface_extra_arrival_is_declared_not_hidden(self) -> None:
        """Codex has one; the point is that it is visible in the row rather
        than spelled out in a guard somewhere."""
        from ai_hats.surfaces import profiles
        from ai_hats.surfaces.hook_channel import BINDABLE_EVENTS

        extra = {e for p in profiles.ALL for e in p.native_events} - set(BINDABLE_EVENTS)
        assert extra == {"PermissionRequest", "Stop", "Notification", "PostInvocation"}


class TestReductionsSettle:
    """The property an if-chain could not offer: the order cannot matter."""

    _CASES = (
        ChainVerdict(decision=ChainDecision.ASK, reason="consent"),
        ChainVerdict(decision=ChainDecision.ASK, updated_input={"command": "TICKET=1 x"}),
        ChainVerdict(decision=ChainDecision.ASK, nudges=(Nudge("hint"),)),
        ChainVerdict(decision=ChainDecision.DENY, reason="late", event=HookEvent.POST_TOOL_USE),
        ChainVerdict(
            decision=ChainDecision.ASK,
            reason="consent",
            updated_input={"command": "x"},
            nudges=(Nudge("hint"),),
            event=HookEvent.POST_TOOL_USE,
        ),
        ChainVerdict(decision=ChainDecision.ALLOW, nudges=(Nudge("hint"),)),
    )

    _DIALECTS = tuple(
        Dialect(can_ask=a, can_ask_with_ticket=t, can_deny_after=d, can_carry_nudges=n)
        for a in (True, False)
        for t in (True, False)
        for d in (True, False)
        for n in (True, False)
    )

    def test_any_order_of_reductions_gives_the_same_verdict(self) -> None:
        import itertools

        from ai_hats.surfaces.hook_channel import REDUCTIONS

        for dialect in self._DIALECTS:
            for verdict in self._CASES:
                seen = {
                    repr(reduce_to(dialect, verdict, reductions=order))
                    for order in itertools.permutations(REDUCTIONS)
                }
                assert len(seen) == 1, (
                    f"order changed the answer for {dialect} on {verdict.decision}: {seen}"
                )

    def test_the_result_is_a_fixed_point(self) -> None:
        """Nothing left to reduce — which is what lets the loop bound be tight."""
        from ai_hats.surfaces.hook_channel import REDUCTIONS

        for dialect in self._DIALECTS:
            for verdict in self._CASES:
                settled = reduce_to(dialect, verdict)
                still = [r.name for r in REDUCTIONS if r.needed(dialect, settled)]
                assert not still, f"{dialect} left {still} unapplied"

    def test_reducing_twice_changes_nothing(self) -> None:
        for dialect in self._DIALECTS:
            for verdict in self._CASES:
                once = reduce_to(dialect, verdict)
                assert reduce_to(dialect, once) == once


class TestRefusalThatArrivesTooLate:
    """`can_deny_after` had no reader until this; cline silently dropped a
    PostToolUse refusal, which the audit counted as a fail-open."""

    _CANNOT_UNDO = Dialect(
        can_ask=False, can_ask_with_ticket=False, can_deny_after=False, can_carry_nudges=True
    )
    _CAN_UNDO = Dialect(
        can_ask=False, can_ask_with_ticket=False, can_deny_after=True, can_carry_nudges=True
    )

    def test_it_becomes_something_the_reader_can_still_act_on(self) -> None:
        late = ChainVerdict(
            decision=ChainDecision.DENY,
            reason="that file is off limits",
            hook="ai-hats:guard",
            event=HookEvent.POST_TOOL_USE,
        )
        told = reduce_to(self._CANNOT_UNDO, late)
        assert told.decision is ChainDecision.ALLOW
        assert told.nudges == (Nudge("that file is off limits", "ai-hats:guard"),)

    def test_a_surface_that_can_undo_still_refuses(self) -> None:
        late = ChainVerdict(decision=ChainDecision.DENY, reason="no", event=HookEvent.POST_TOOL_USE)
        assert reduce_to(self._CAN_UNDO, late).decision is ChainDecision.DENY

    def test_a_refusal_before_the_call_is_untouched(self) -> None:
        """The control: only a call that ALREADY RAN is too late to stop."""
        early = ChainVerdict(decision=ChainDecision.DENY, reason="no", event=HookEvent.PRE_TOOL_USE)
        assert reduce_to(self._CANNOT_UNDO, early).decision is ChainDecision.DENY
