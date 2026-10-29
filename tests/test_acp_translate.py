"""The substitutions an ACP holder makes, with no process on either side."""

from __future__ import annotations

from ai_hats.acp.translate import (
    AGENT_KEY,
    DENIAL_PHRASE,
    LOG_KEY,
    SKILLS_KEY,
    classify_commands,
    elicitation_declared,
    permission_for_client,
    forward_update,
    mode_options,
    project_commands,
    prompt_text,
    rejection_verdict,
    usage_update,
    used_tokens,
    window_size,
    with_log_path,
    with_mode_options,
)


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


def test_elicitation_is_the_clients_declared_capability() -> None:
    assert elicitation_declared({"clientCapabilities": {"elicitation": {"form": True}}})
    assert not elicitation_declared({})


def _permission(call_id: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "session/request_permission",
        "params": {
            "sessionId": "s",
            "toolCall": {"toolCallId": call_id, "title": "rm"},
            "options": [],
        },
    }


def test_a_child_id_is_attached_only_when_it_is_already_known() -> None:
    stranger = permission_for_client(
        _permission("call-2"), claimed="untrusted", known_ids=set(), by_call={}
    )
    assert AGENT_KEY not in (stranger["params"]["toolCall"].get("_meta") or {})

    matched = permission_for_client(
        _permission("call-2"),
        claimed="child-from-journal",
        known_ids={"child-from-journal"},
        by_call={},
    )
    assert matched["params"]["toolCall"]["_meta"] == {AGENT_KEY: "child-from-journal"}

    same_call = permission_for_client(
        _permission("spawn-1"),
        claimed="untrusted",
        known_ids={"child-from-journal"},
        by_call={"spawn-1": "child-from-journal"},
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
