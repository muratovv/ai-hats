"""Grok's ACP child: argv, questions, and the id a tool result named."""

from __future__ import annotations

from ai_hats.acp.translate import AGENT_KEY, AskClient, ReplyToChild, permission_for_client
from ai_hats.surfaces.grok.acp_child import GrokAcpChild, UNTRUSTED_CHILD_KEY


def test_argv_is_the_session_child_and_drops_the_one_shot_flags() -> None:
    planned = [
        "grok",
        "--session-id",
        "11111111-1111-1111-1111-111111111111",
        "--model",
        "grok-4.7",
        "--always-approve",
        "-p",
        "hello",
    ]

    assert GrokAcpChild().argv(planned) == [
        "grok",
        "--permission-mode",
        "default",
        "agent",
        "--no-leader",
        "stdio",
        "--model",
        "grok-4.7",
    ]


def test_free_text_without_elicitation_is_refused_without_asking() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "_x.ai/ask_user_question",
        "params": {"questions": [{"question": "Why?", "options": []}]},
    }

    decision = GrokAcpChild().present_question(request, elicitation=False)

    assert isinstance(decision, ReplyToChild)
    assert decision.result == {"outcome": "declined"}


def test_options_without_elicitation_become_a_permission() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "_x.ai/ask_user_question",
        "params": {
            "sessionId": "s",
            "toolCallId": "q1",
            "questions": [
                {"question": "Which color?", "options": [{"label": "red"}, {"label": "blue"}]}
            ],
        },
    }

    child = GrokAcpChild()
    decision = child.present_question(request, elicitation=False)

    assert isinstance(decision, AskClient)
    assert decision.message["method"] == "session/request_permission"
    answer = child.answer_for_child(decision.original, {"outcome": "selected", "optionId": "red"})
    assert answer == {"outcome": "accepted", "answers": {"Which color?": ["red"]}}


def test_elicitation_is_used_when_the_client_declared_it() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "_x.ai/ask_user_question",
        "params": {"questions": [{"question": "Why?", "options": []}]},
    }

    decision = GrokAcpChild().present_question(request, elicitation=True)

    assert isinstance(decision, AskClient)
    assert decision.message["method"] == "elicitation/create"


def _permission(call_id: str, claimed: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "session/request_permission",
        "params": {
            "sessionId": "s",
            "toolCall": {
                "toolCallId": call_id,
                "title": "rm",
                "_meta": {UNTRUSTED_CHILD_KEY: claimed},
            },
            "options": [],
        },
    }


def test_an_untrusted_child_id_is_omitted_until_a_tool_result_names_it() -> None:
    child = GrokAcpChild()
    stranger_message, claimed = child.untrusted_child(_permission("call-2", "untrusted"))
    stranger = permission_for_client(stranger_message, claimed=claimed, known_ids=set(), by_call={})
    assert UNTRUSTED_CHILD_KEY not in (stranger["params"]["toolCall"].get("_meta") or {})
    assert AGENT_KEY not in (stranger["params"]["toolCall"].get("_meta") or {})

    update = {"toolCallId": "spawn-1", "rawOutput": {"subagent_id": "child-from-journal"}}
    known = {child.child_id_from_update(update) or ""}
    by_call = {"spawn-1": "child-from-journal"}
    matched_message, claimed = child.untrusted_child(_permission("call-2", "child-from-journal"))
    matched = permission_for_client(
        matched_message, claimed=claimed, known_ids=known, by_call=by_call
    )
    meta = matched["params"]["toolCall"]["_meta"]
    assert meta == {AGENT_KEY: "child-from-journal"}
    assert child.child_result_content("child-from-journal") == {"subagent_id": "child-from-journal"}

    same_message, claimed = child.untrusted_child(_permission("spawn-1", "untrusted"))
    same_call = permission_for_client(
        same_message, claimed=claimed, known_ids=known, by_call=by_call
    )
    assert same_call["params"]["toolCall"]["_meta"][AGENT_KEY] == "child-from-journal"
