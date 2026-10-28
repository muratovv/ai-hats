"""Pure substitutions between the client's ACP and grok's ACP.

Nothing here touches a process. The relay asks these functions what to write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ai_hats_observe.canonical import (
    AgentId,
    GateDecision,
    GatePoint,
    GateVerdict,
    ToolCallId,
)
from ai_hats_observe.canonical.types import now

# The holder's phrase for an empty denial. The permission result has no text field,
# so the words live in the journal, which is where the holder already records them.
DENIAL_PHRASE = "The person driving this session denied this call."

LOG_KEY = "ai-hats/log"
AGENT_KEY = "ai-hats/agent"
SKILLS_KEY = "ai-hats/skills"
UNTRUSTED_CHILD_KEY = "x.ai/requestingSessionId"

_REJECT = frozenset({"reject_once", "reject_always"})
_OURS = frozenset({"plan", "permission_mode"})


@dataclass(frozen=True)
class AskClient:
    """Show `message` to the client. `original` is the child's request, for the answer."""

    message: dict[str, Any]
    original: dict[str, Any]


@dataclass(frozen=True)
class ReplyToChild:
    """Answer the child now. The client is not asked, so the turn cannot sit open."""

    result: dict[str, Any]


def message_kind(message: dict[str, Any]) -> str:
    """`request`, `notification`, `response`, or `invalid`."""
    has_method = "method" in message
    has_id = "id" in message
    if has_method and has_id:
        return "request"
    if has_method:
        return "notification"
    if has_id:
        return "response"
    return "invalid"


def elicitation_declared(params: dict[str, Any] | None) -> bool:
    """Whether the client's initialize announced it can answer an elicitation."""
    caps = (params or {}).get("clientCapabilities") or {}
    return bool(caps.get("elicitation"))


def prompt_text(params: dict[str, Any] | None) -> str:
    """The person's words inside a `session/prompt`, text blocks in order."""
    parts: list[str] = []
    for block in (params or {}).get("prompt") or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def prompt_id(params: dict[str, Any] | None) -> str:
    """A canonical UUID for the journal. The client's own, when it sent one."""
    raw = ((params or {}).get("_meta") or {}).get("promptId")
    if isinstance(raw, str):
        try:
            return str(uuid.UUID(raw))
        except ValueError:
            pass
    return str(uuid.uuid4())


def window_size(payload: dict[str, Any] | None) -> int | None:
    """The current model's context window, from initialize or session/new."""
    body = payload or {}
    meta = body.get("_meta") or {}
    state = meta.get("modelState") if isinstance(meta, dict) else None
    models: list[Any] = []
    current: str | None = None
    if isinstance(state, dict):
        models = list(state.get("availableModels") or [])
        raw_current = state.get("currentModelId")
        current = raw_current if isinstance(raw_current, str) else None
    listed = body.get("models")
    if isinstance(listed, dict):
        models = list(listed.get("availableModels") or models)
        raw_current = listed.get("currentModelId")
        if isinstance(raw_current, str):
            current = raw_current
    chosen: dict[str, Any] | None = None
    for model in models:
        if isinstance(model, dict) and model.get("modelId") == current:
            chosen = model
            break
    if chosen is None:
        for model in models:
            if isinstance(model, dict):
                chosen = model
                break
    if chosen is None:
        return None
    raw = (chosen.get("_meta") or {}).get("totalContextTokens")
    if isinstance(raw, int) and raw > 0:
        return raw
    return None


def used_tokens(prompt_result: dict[str, Any] | None) -> int | None:
    """How full the context is, from the prompt result's own token count."""
    raw = ((prompt_result or {}).get("_meta") or {}).get("inputTokens")
    if isinstance(raw, int) and raw >= 0:
        return raw
    return None


def with_log_path(result: dict[str, Any], log_path: str) -> dict[str, Any]:
    """session/new result plus the journal path. The client's id is the agent's."""
    meta = dict(result.get("_meta") or {})
    meta[LOG_KEY] = log_path
    return {**result, "_meta": meta}


def mode_options(*, plan: str = "off", permission_mode: str = "default") -> list[dict[str, Any]]:
    """Plan and the permission-mode word, both category `mode`."""
    return [
        {
            "id": "plan",
            "name": "Plan",
            "category": "mode",
            "type": "select",
            "currentValue": plan,
            "options": [
                {"value": "off", "name": "Off"},
                {"value": "on", "name": "Plan"},
            ],
        },
        {
            "id": "permission_mode",
            "name": "Permissions",
            "category": "mode",
            "type": "select",
            "currentValue": permission_mode,
            "options": [
                {"value": "default", "name": "Default"},
                {"value": "auto", "name": "Auto"},
            ],
        },
    ]


def with_mode_options(
    result: dict[str, Any], *, plan: str = "off", permission_mode: str = "default"
) -> dict[str, Any]:
    """Append our mode options to whatever config the child already published."""
    existing = list(result.get("configOptions") or [])
    existing.extend(mode_options(plan=plan, permission_mode=permission_mode))
    return {**result, "configOptions": existing}


def our_config(config_id: object) -> bool:
    """A config id the holder answers itself, because the child has no such option."""
    return config_id in _OURS


def usage_update(session_id: str, used: int, size: int) -> dict[str, Any]:
    """The notification a client reads before the prompt's stopReason."""
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": session_id,
            "update": {"sessionUpdate": "usage_update", "used": used, "size": size},
        },
    }


def forward_update(update: dict[str, Any], person_texts: set[str]) -> bool:
    """user_message leaves only when its text is a prompt the person sent."""
    kind = update.get("sessionUpdate")
    if kind not in ("user_message", "user_message_chunk"):
        return True
    content = update.get("content")
    text = ""
    if isinstance(content, dict):
        text = str(content.get("text") or "")
    elif isinstance(content, str):
        text = content
    if not text:
        return False
    return any(text == person or text in person for person in person_texts)


def classify_commands(update: dict[str, Any], skill_names: set[str]) -> tuple[list[str], list[str]]:
    """Grok commands stay commands. A name is a skill only when this session installed it."""
    commands: list[str] = []
    skills: list[str] = []
    for command in update.get("availableCommands") or []:
        if not isinstance(command, dict):
            continue
        name = command.get("name")
        if not isinstance(name, str) or not name:
            continue
        if not name.startswith("/") and name in skill_names:
            skills.append(name)
        else:
            commands.append(name)
    return commands, skills


def project_commands(update: dict[str, Any], *, skill_names: set[str]) -> dict[str, Any]:
    """The update, plus the installed skill names under their own meta key."""
    _commands, skills = classify_commands(update, skill_names)
    if not skills:
        return update
    meta = dict(update.get("_meta") or {})
    meta[SKILLS_KEY] = skills
    return {**update, "_meta": meta}


def child_id_from_update(update: dict[str, Any]) -> str | None:
    """A sub-agent id the tool result itself named. Never the permission's meta key."""
    raw = update.get("rawOutput")
    if not isinstance(raw, dict):
        return None
    sid = raw.get("subagent_id")
    if isinstance(sid, str) and sid:
        return sid
    return None


def permission_for_client(
    message: dict[str, Any],
    *,
    known_ids: set[str],
    by_call: dict[str, str],
) -> dict[str, Any]:
    """Drop the untrusted child id. Attach one only when a tool result already named it."""
    params = dict(message.get("params") or {})
    tool = dict(params.get("toolCall") or {})
    meta = dict(tool.get("_meta") or {})
    claimed = meta.pop(UNTRUSTED_CHILD_KEY, None)
    call_id = tool.get("toolCallId")
    trusted = by_call.get(call_id) if isinstance(call_id, str) else None
    if trusted is None and isinstance(claimed, str) and claimed in known_ids:
        trusted = claimed
    if isinstance(trusted, str) and trusted:
        meta[AGENT_KEY] = trusted
    elif meta.get(AGENT_KEY) not in known_ids:
        meta.pop(AGENT_KEY, None)
    if meta:
        tool["_meta"] = meta
    else:
        tool.pop("_meta", None)
    params["toolCall"] = tool
    return {**message, "params": params}


def option_kind(params: dict[str, Any], option_id: object) -> str | None:
    """The kind of the option the client selected."""
    for option in params.get("options") or []:
        if isinstance(option, dict) and option.get("optionId") == option_id:
            kind = option.get("kind")
            return kind if isinstance(kind, str) else None
    return None


def rejection_verdict(original: dict[str, Any], result: dict[str, Any]) -> GateVerdict | None:
    """The journal line for an empty reject. No line for an allow or a non-permission."""
    if original.get("method") != "session/request_permission":
        return None
    if result.get("outcome") != "selected":
        return None
    if option_kind(original.get("params") or {}, result.get("optionId")) not in _REJECT:
        return None
    tool = (original.get("params") or {}).get("toolCall") or {}
    call = tool.get("toolCallId")
    return GateVerdict(
        point=GatePoint.BEFORE_TOOL,
        decision=GateDecision.DENY,
        hook="person",
        reason=DENIAL_PHRASE,
        tool=str(tool.get("title") or tool.get("kind") or ""),
        call_id=ToolCallId(str(call)) if isinstance(call, str) and call else None,
        source="acp",
        agent=AgentId(str(tool.get("_meta", {}).get(AGENT_KEY)))
        if isinstance((tool.get("_meta") or {}).get(AGENT_KEY), str)
        else None,
        ts=now(),
    )


def present_question(request: dict[str, Any], *, elicitation: bool) -> AskClient | ReplyToChild:
    """What the client is shown for a child's question, or an immediate refusal."""
    method = request.get("method")
    if method != "_x.ai/ask_user_question":
        return AskClient(request, request)
    params = request.get("params") or {}
    questions = [q for q in params.get("questions") or [] if isinstance(q, dict)]
    if elicitation and questions:
        return AskClient(_elicitation(request, questions), request)
    if len(questions) == 1 and questions[0].get("options"):
        return AskClient(_permission_for_question(request, questions[0]), request)
    return ReplyToChild({"outcome": "declined"})


def answer_for_child(original: dict[str, Any], client_result: dict[str, Any]) -> dict[str, Any]:
    """The result body the child reads. A permission result passes through unchanged."""
    if original.get("method") != "_x.ai/ask_user_question":
        return client_result
    params = original.get("params") or {}
    action = client_result.get("action")
    if action in ("decline", "cancel") or client_result.get("outcome") == "cancelled":
        return {"outcome": "declined"}
    if action == "accept":
        return {"outcome": "accepted", "answers": _accepted_answers(params, client_result)}
    if client_result.get("outcome") == "selected":
        question = _question_text(params)
        option_id = client_result.get("optionId")
        picked = option_id if isinstance(option_id, str) else ""
        return {"outcome": "accepted", "answers": {question: [picked]}}
    return {"outcome": "declined"}


def _elicitation(request: dict[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for question in questions:
        name = _one_question(question)
        labels = [
            option.get("label")
            for option in question.get("options") or []
            if isinstance(option, dict) and isinstance(option.get("label"), str)
        ]
        schema: dict[str, Any] = {"type": "string", "description": name}
        if labels:
            schema["enum"] = labels
        properties[name] = schema
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "method": "elicitation/create",
        "params": {
            "mode": "form",
            "message": _one_question(questions[0]),
            "requestedSchema": {"type": "object", "properties": properties},
        },
    }


def _permission_for_question(request: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params") or {}
    options = []
    for option in question.get("options") or []:
        if not isinstance(option, dict):
            continue
        label = option.get("label")
        if not isinstance(label, str):
            continue
        options.append({"optionId": label, "name": label, "kind": "allow_once"})
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "method": "session/request_permission",
        "params": {
            "sessionId": params.get("sessionId"),
            "toolCall": {
                "toolCallId": params.get("toolCallId"),
                "title": _one_question(question),
                "kind": "other",
                "rawInput": {},
            },
            "options": options,
        },
    }


def _accepted_answers(
    params: dict[str, Any], client_result: dict[str, Any]
) -> dict[str, list[str]]:
    content = client_result.get("content")
    if isinstance(content, dict) and content:
        answers: dict[str, list[str]] = {}
        for key, value in content.items():
            if isinstance(value, list):
                answers[str(key)] = [str(item) for item in value]
            else:
                answers[str(key)] = [str(value)]
        return answers
    question = _question_text(params)
    return {question: []}


def _question_text(params: dict[str, Any]) -> str:
    questions = [q for q in params.get("questions") or [] if isinstance(q, dict)]
    if not questions:
        return ""
    return _one_question(questions[0])


def _one_question(question: dict[str, Any]) -> str:
    text = question.get("question")
    return text if isinstance(text, str) else ""
