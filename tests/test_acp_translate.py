"""The substitutions an ACP holder makes, with no process on either side."""

from __future__ import annotations

from ai_hats.acp.argv import grok_acp_argv
from ai_hats.acp.translate import (
    AGENT_KEY,
    DENIAL_PHRASE,
    LOG_KEY,
    SKILLS_KEY,
    UNTRUSTED_CHILD_KEY,
    AskClient,
    ReplyToChild,
    answer_for_child,
    child_id_from_update,
    classify_commands,
    elicitation_declared,
    permission_for_client,
    forward_update,
    mode_options,
    present_question,
    project_commands,
    prompt_text,
    rejection_verdict,
    usage_update,
    used_tokens,
    window_size,
    with_log_path,
    with_mode_options,
)


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

    assert grok_acp_argv(planned) == [
        "grok",
        "agent",
        "--no-leader",
        "stdio",
        "--model",
        "grok-4.7",
    ]


def test_session_new_carries_the_log_and_the_mode_words() -> None:
    result = with_mode_options(with_log_path({"sessionId": "s", "_meta": {"cwd": "/tmp"}}, "/log"))

    assert result["sessionId"] == "s"
    assert result["_meta"][LOG_KEY] == "/log"
    assert result["_meta"]["cwd"] == "/tmp"
    ids = [option["id"] for option in result["configOptions"]]
    assert ids == ["plan", "permission_mode"]
    assert {option["category"] for option in result["configOptions"]} == {"mode"}


def test_prompt_text_joins_text_blocks() -> None:
    text = prompt_text(
        {
            "prompt": [
                {"type": "text", "text": "one"},
                {"type": "image"},
                {"type": "text", "text": "two"},
            ]
        }
    )

    assert text == "one\ntwo"


def test_user_message_passes_only_the_persons_words() -> None:
    person = {"reply with pong"}

    assert forward_update({"sessionUpdate": "agent_message_chunk"}, person)
    assert forward_update(
        {"sessionUpdate": "user_message", "content": {"type": "text", "text": "reply with pong"}},
        person,
    )
    assert not forward_update(
        {"sessionUpdate": "user_message", "content": {"type": "text", "text": "skill body"}},
        person,
    )


def test_usage_update_uses_the_window_and_the_prompt_fill() -> None:
    payload = {
        "_meta": {
            "modelState": {
                "currentModelId": "grok-4.7",
                "availableModels": [
                    {"modelId": "grok-4.7", "_meta": {"totalContextTokens": 256000}}
                ],
            }
        }
    }

    assert window_size(payload) == 256000
    assert used_tokens({"_meta": {"inputTokens": 10}}) == 10
    note = usage_update("s", 10, 256000)
    assert note["params"]["update"] == {
        "sessionUpdate": "usage_update",
        "used": 10,
        "size": 256000,
    }


def test_empty_reject_is_the_holders_phrase_and_allow_is_not() -> None:
    request = {
        "method": "session/request_permission",
        "params": {
            "toolCall": {"toolCallId": "c1", "title": "rm"},
            "options": [
                {"optionId": "allow-once", "kind": "allow_once"},
                {"optionId": "reject-once", "kind": "reject_once"},
            ],
        },
    }

    denied = rejection_verdict(request, {"outcome": "selected", "optionId": "reject-once"})
    allowed = rejection_verdict(request, {"outcome": "selected", "optionId": "allow-once"})

    assert denied is not None and denied.reason == DENIAL_PHRASE
    assert allowed is None


def test_free_text_without_elicitation_is_refused_without_asking() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "_x.ai/ask_user_question",
        "params": {"questions": [{"question": "Why?", "options": []}]},
    }

    decision = present_question(request, elicitation=False)

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

    decision = present_question(request, elicitation=False)

    assert isinstance(decision, AskClient)
    assert decision.message["method"] == "session/request_permission"
    answer = answer_for_child(decision.original, {"outcome": "selected", "optionId": "red"})
    assert answer == {"outcome": "accepted", "answers": {"Which color?": ["red"]}}


def test_elicitation_is_used_when_the_client_declared_it() -> None:
    assert elicitation_declared({"clientCapabilities": {"elicitation": {"form": True}}})
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "_x.ai/ask_user_question",
        "params": {"questions": [{"question": "Why?", "options": []}]},
    }

    decision = present_question(request, elicitation=True)

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
    stranger = permission_for_client(
        _permission("call-2", "untrusted"), known_ids=set(), by_call={}
    )
    assert UNTRUSTED_CHILD_KEY not in (stranger["params"]["toolCall"].get("_meta") or {})
    assert AGENT_KEY not in (stranger["params"]["toolCall"].get("_meta") or {})

    update = {"toolCallId": "spawn-1", "rawOutput": {"subagent_id": "child-from-journal"}}
    known = {child_id_from_update(update) or ""}
    by_call = {"spawn-1": "child-from-journal"}
    matched = permission_for_client(
        _permission("call-2", "child-from-journal"), known_ids=known, by_call=by_call
    )
    meta = matched["params"]["toolCall"]["_meta"]
    assert meta == {AGENT_KEY: "child-from-journal"}

    same_call = permission_for_client(
        _permission("spawn-1", "untrusted"), known_ids=known, by_call=by_call
    )
    assert same_call["params"]["toolCall"]["_meta"][AGENT_KEY] == "child-from-journal"


def test_builtin_grok_commands_stay_commands() -> None:
    update = {
        "sessionUpdate": "available_commands_update",
        "availableCommands": [
            {"name": "compact"},
            {"name": "flush"},
            {"name": "hatrack"},
            {"name": "/compact"},
        ],
    }

    commands, skills = classify_commands(update, {"hatrack"})
    projected = project_commands(update, skill_names={"hatrack"})

    assert commands == ["compact", "flush", "/compact"]
    assert skills == ["hatrack"]
    assert projected["_meta"][SKILLS_KEY] == ["hatrack"]
    assert [item["name"] for item in projected["availableCommands"]] == [
        "compact",
        "flush",
        "hatrack",
        "/compact",
    ]


def test_mode_options_name_plan_and_the_permission_word() -> None:
    options = mode_options(plan="on", permission_mode="auto")

    assert options[0]["currentValue"] == "on"
    assert options[1]["currentValue"] == "auto"
