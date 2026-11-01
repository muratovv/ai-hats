"""Render an operation's consent requirement through a registered delivery channel."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ConsentQuestion:
    operation: str
    args: tuple[str, ...]
    anchor: str
    ordinal: int
    total: int
    subject: str
    headline: str


@dataclass(frozen=True)
class FormTransport:
    id: str
    provider: str
    operation: str
    module: str
    tool: str


_FORMS: dict[tuple[str, str], FormTransport] = {}


def register_form(transport: FormTransport) -> FormTransport:
    key = (transport.provider, transport.operation)
    if key in _FORMS:
        raise ValueError(f"Form transport already registered: {key}")
    _FORMS[key] = transport
    return transport


RACK_FORM = register_form(
    FormTransport(
        id="codex.rack_transition",
        provider="codex",
        operation="rack.transition",
        module="ai_hats.consent_mcp.server",
        tool="ai_hats_consent.rack_transition",
    )
)


def present_question(
    question: ConsentQuestion,
    *,
    provider: str,
    transport_id: str,
    issue: Callable[[ConsentQuestion], dict],
) -> dict:
    transport = _FORMS.get((provider, question.operation))
    if transport is not None and transport.id == transport_id:
        return {"permissionDecision": "ask", "permissionDecisionReason": question.headline}
    answer = issue(question)
    if transport is not None:
        answer = {
            **answer,
            "permissionDecisionReason": answer["permissionDecisionReason"]
            + f" In an interactive session, use {transport.tool} with the operation arguments as a list.",
        }
    return answer


def protected_server_launch(args: Sequence[str]) -> bool:
    return any(
        transport.module in token
        or token.endswith("/" + transport.module.replace(".", "/") + ".py")
        for transport in _FORMS.values()
        for token in args
    )
