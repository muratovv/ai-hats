"""The agy→claude bridge, in the shape codex already proved (HATS-1776).

Three translations, and the third is where agy goes past codex's precedent: a
verdict may REWRITE the tool's input (the consent ticket, HATS-1642), so the
answer has to leave in the key the surface spoke in.
"""

from __future__ import annotations

from ai_hats_agy.claude_hook_adapter import (
    agy_tool_name,
    from_claude_decision,
    matches_claude_hook,
    to_claude_payload,
)

AGY_BASH = {
    "hook_event_name": "PreToolUse",
    "toolCall": {"name": "run_command", "args": {"CommandLine": "git push --force"}},
}
CLAUDE_BASH = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "git push --force"},
}


class TestToolNames:
    def test_the_terminal_tool_answers_to_the_claude_matcher(self):
        """The measured miss: every shipped Bash row said `Bash`, agy calls it
        `run_command`, and the dispatcher compared the two literally."""
        assert matches_claude_hook("Bash", "run_command")

    def test_the_file_mutation_class_answers_too(self):
        """The names AGY_FILE_MUTATION_MATCHER enumerated in the provider — the
        one axis that was already translated, now beside the other."""
        for tool in ("Create", "write_to_file", "replace_file_content"):
            assert matches_claude_hook("Edit|Write|MultiEdit", tool), tool

    def test_a_row_for_another_class_still_does_not_match(self):
        assert not matches_claude_hook("Bash", "write_to_file")
        assert not matches_claude_hook("Edit|Write|MultiEdit", "run_command")

    def test_a_claude_name_matches_itself(self):
        """The same dispatcher serves payloads already in claude vocabulary."""
        assert matches_claude_hook("Bash", "Bash")

    def test_match_all_matches(self):
        assert matches_claude_hook("*", "run_command")
        assert matches_claude_hook("", "run_command")


class TestToolNameFromPayload:
    def test_the_name_is_read_from_the_payload_not_from_argv(self):
        """argv is a contract nothing guarantees: agy may pass the name, and when
        it does not, the dispatcher's matcher filter silently switched OFF and
        every hook ran on every call. The payload always carries it."""
        assert agy_tool_name(AGY_BASH) == "run_command"
        assert agy_tool_name(CLAUDE_BASH) == "Bash"

    def test_a_payload_naming_nothing_answers_empty(self):
        assert agy_tool_name({}) == ""


class TestRequestDirection:
    def test_the_agy_dialect_arrives_as_the_claude_one(self):
        adapted = to_claude_payload(AGY_BASH)

        assert adapted["tool_input"] == {"command": "git push --force"}
        assert adapted["tool_name"] == "Bash"

    def test_a_claude_payload_passes_through_unharmed(self):
        assert to_claude_payload(CLAUDE_BASH)["tool_input"] == {"command": "git push --force"}

    def test_the_path_spellings_collapse_onto_the_claude_one(self):
        """`backlog_write_gate` and `wt_gate` each fan out over five spellings of
        one path today. One name reaches them now."""
        for spoken in ("TargetFile", "AbsolutePath", "target_file"):
            adapted = to_claude_payload({"toolCall": {"name": "Create", "args": {spoken: "/x.py"}}})
            assert adapted["tool_input"]["file_path"] == "/x.py", spoken

    def test_a_claude_key_already_present_is_never_overwritten(self):
        adapted = to_claude_payload(
            {"toolCall": {"name": "Create", "args": {"file_path": "/keep", "TargetFile": "/drop"}}}
        )

        assert adapted["tool_input"]["file_path"] == "/keep"

    def test_the_other_args_ride_along(self):
        """A guard may read more than the command — `cwd`, a path, a flag."""
        adapted = to_claude_payload(
            {"toolCall": {"name": "run_command", "args": {"CommandLine": "ls", "Cwd": "/tmp"}}}
        )

        assert adapted["tool_input"]["Cwd"] == "/tmp"


class TestDecisionDirection:
    def test_a_rewritten_input_leaves_in_the_key_the_surface_spoke(self):
        """HATS-1642, now owned by the bridge instead of by the gate: agy spells
        the argument `CommandLine`, and a ticket written back as `command` is a
        consent gate that silently stops arming."""
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "updatedInput": {"command": "AI_HATS_CONSENT_TICKET=x git push"},
            }
        }

        out = from_claude_decision(decision, AGY_BASH)

        updated = out["hookSpecificOutput"]["updatedInput"]
        assert updated == {"CommandLine": "AI_HATS_CONSENT_TICKET=x git push"}

    def test_a_decision_without_a_rewrite_is_untouched(self):
        decision = {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}
        }

        assert from_claude_decision(decision, AGY_BASH) == decision

    def test_a_claude_payload_keeps_the_claude_key(self):
        decision = {"hookSpecificOutput": {"updatedInput": {"command": "x"}}}

        out = from_claude_decision(decision, CLAUDE_BASH)

        assert out["hookSpecificOutput"]["updatedInput"] == {"command": "x"}
