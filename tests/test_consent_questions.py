from ai_hats_library.hooks.consent_gate.questions import ConsentQuestion, present_question
import pytest


def test_form_delivery_does_not_issue_ticket_before_accept():
    question = ConsentQuestion(
        "rack.transition",
        ("rack", "transition", "T-1", "execute"),
        "rack",
        0,
        1,
        "T-1",
        "T-1 needs consent",
    )

    def issue(_question):
        raise AssertionError("A pending form must not issue a ticket")

    answer = present_question(
        question, provider="codex", transport_id="codex.rack_transition", issue=issue
    )

    assert answer == {"permissionDecision": "ask", "permissionDecisionReason": "T-1 needs consent"}


@pytest.mark.parametrize(
    "provider,operation,marker",
    [
        ("claude", "rack.transition", "codex.rack_transition"),
        ("codex", "wt.merge", "codex.rack_transition"),
        ("codex", "rack.transition", "unknown"),
    ],
)
def test_unmatched_transport_preserves_ticket_delivery(provider, operation, marker):
    question = ConsentQuestion(operation, ("rack",), "rack", 0, 1, "T-1", "Approve")
    issued = []

    def issue(request):
        issued.append(request)
        return {
            "permissionDecision": "ask",
            "permissionDecisionReason": "Approve",
            "updatedInput": {"command": "ticket-bound command"},
        }

    result = present_question(question, provider=provider, transport_id=marker, issue=issue)

    assert issued == [question]
    assert result["updatedInput"] == {"command": "ticket-bound command"}
