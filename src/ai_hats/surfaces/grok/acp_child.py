"""The grok process an ACP holder spawns, and the fields only that process speaks."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_hats.acp.translate import AskClient, ReplyToChild

from .home import GROK_HOME_ENV
from .session_config import asking_config

if TYPE_CHECKING:
    from ai_hats_observe import Session

_MODEL = {"--model", "-m"}
# Flags the one-shot launch uses. This child is a session, so none of them belong.
_DROP_WITH_VALUE = {"--session-id"}
_DROP = {"--always-approve", "-p", "--print"}

QUESTION_METHOD = "_x.ai/ask_user_question"
UNTRUSTED_CHILD_KEY = "x.ai/requestingSessionId"


class GrokAcpChild:
    """What the ACP holder asks grok, and only grok, to do."""

    def argv(self, planned: list[str]) -> list[str]:
        """Session argv plus a model from the plan. One-shot flags and `--session-id` are dropped."""
        binary = planned[0] if planned else "grok"
        model: str | None = None
        args = planned[1:]
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in _MODEL and index + 1 < len(args):
                model = args[index + 1]
                index += 2
                continue
            if arg.startswith("--model="):
                model = arg.split("=", 1)[1]
                index += 1
                continue
            if arg in _DROP_WITH_VALUE and index + 1 < len(args):
                index += 2
                continue
            if arg in _DROP or arg in _DROP_WITH_VALUE:
                index += 1
                continue
            index += 1
        # `default` prompts, and a flag beats the person's config.toml mode.
        argv = [binary, "--permission-mode", "default", "agent", "--no-leader", "stdio"]
        if model:
            argv.extend(["--model", model])
        return argv

    def planned_session_id(self, minted: str) -> str:
        """Empty: grok names the session in session/new, and a minted id publishes an empty directory."""
        del minted
        return ""

    def prepare_spawn(self, env: dict[str, str]) -> None:
        """The session file asks. A person's auto would skip session/request_permission."""
        home = env.get(GROK_HOME_ENV)
        if not home:
            return
        path = Path(home) / "config.toml"
        if not path.is_file():
            return
        path.write_text(asking_config(path.read_text(encoding="utf-8")), encoding="utf-8")

    def retain_transcript(self, env: dict[str, str], cwd: Path, session: Session) -> None:
        """The id arrives from session/new, so the launch-time bind never published the file."""
        ids = session.provider_session_ids()
        home = env.get(GROK_HOME_ENV)
        if not ids or not home:
            return
        from .provider import GrokSurface

        GrokSurface().retain_transcript(Path(home), Path(cwd), ids[-1])

    def session_result(
        self, method: str, result: dict[str, Any], resume_id: str | None
    ) -> dict[str, Any]:
        """grok 1.0.70 omits sessionId on resume; the request already named it."""
        if method != "session/resume" or not resume_id:
            return result
        named = result.get("sessionId")
        if isinstance(named, str) and named:
            return result
        return {**result, "sessionId": resume_id}

    def present_question(
        self, request: dict[str, Any], *, elicitation: bool
    ) -> AskClient | ReplyToChild:
        """What the client is shown for a question, or a refusal. Other methods pass through."""
        if request.get("method") != QUESTION_METHOD:
            return AskClient(request, request)
        params = request.get("params") or {}
        questions = [q for q in params.get("questions") or [] if isinstance(q, dict)]
        if elicitation and questions:
            return AskClient(_elicitation(request, questions), request)
        if len(questions) == 1 and questions[0].get("options"):
            return AskClient(_permission_for_question(request, questions[0]), request)
        return ReplyToChild({"outcome": "declined"})

    def answer_for_child(
        self, original: dict[str, Any], client_result: dict[str, Any]
    ) -> dict[str, Any]:
        """The result body the child reads. A permission result passes through unchanged."""
        if original.get("method") != QUESTION_METHOD:
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

    def child_id_from_update(self, update: dict[str, Any]) -> str | None:
        """A sub-agent id the tool result itself named. Never the permission's meta key."""
        raw = update.get("rawOutput")
        if not isinstance(raw, dict):
            return None
        sid = raw.get("subagent_id")
        if isinstance(sid, str) and sid:
            return sid
        return None

    def child_result_content(self, child_id: str) -> dict[str, Any]:
        """What the journal records for a child the tool result named."""
        return {"subagent_id": child_id}

    def untrusted_child(self, message: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        """Drop ``x.ai/requestingSessionId``. Return the message and the value it held."""
        params = dict(message.get("params") or {})
        tool = dict(params.get("toolCall") or {})
        meta = dict(tool.get("_meta") or {})
        claimed = meta.pop(UNTRUSTED_CHILD_KEY, None)
        if meta:
            tool["_meta"] = meta
        else:
            tool.pop("_meta", None)
        params["toolCall"] = tool
        return {**message, "params": params}, claimed


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
