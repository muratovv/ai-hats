"""Pure substitutions between the client's ACP and the child's.

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


def kept_options(options: object) -> list[dict[str, Any]]:
    """Child options, without the two the holder answers itself."""
    if not isinstance(options, list):
        return []
    return [item for item in options if isinstance(item, dict) and item.get("id") not in _OURS]


def full_config(child: list[dict[str, Any]], *, plan: str, permission_mode: str) -> dict[str, Any]:
    """The complete list a set_config_option response carries."""
    return {"configOptions": [*child, *mode_options(plan=plan, permission_mode=permission_mode)]}


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
    """A name is a skill only when this session installed it. The rest stay commands."""
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


def permission_for_client(
    message: dict[str, Any],
    *,
    claimed: Any,
    known_ids: set[str],
    by_call: dict[str, str],
) -> dict[str, Any]:
    """Attach `claimed` only when a tool result already named that id."""
    params = dict(message.get("params") or {})
    tool = dict(params.get("toolCall") or {})
    meta = dict(tool.get("_meta") or {})
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
