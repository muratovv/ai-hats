"""Contract guard: WrapRunner.run has NO ``system_prompt_override`` parameter.

HATS-452 / П2 in ADR-0005: ``WrapRunner`` is the HITL runner — prompt
injection is meaningless here (the user types into the terminal) and
the previously-exposed Optional override was the literal trap that
caused HATS-452. The trap is physically removed; this test prevents a
future refactor from re-adding it.

If a sub-agent / automation use case needs to inject an explicit prompt,
use ``SubAgentRunner`` (Automate path) which takes a required ``task``
argument — that's the correct surface for HATS-267-style prompt
injection.
"""

from __future__ import annotations

import inspect

from ai_hats.runtime import SubAgentRunner, WrapRunner


def test_wraprunner_run_has_no_system_prompt_override():
    """The trap that caused HATS-452 — an Optional override param on the
    HITL runner whose empty string ambiguously meant both "absent" and
    "override with empty" — must not exist on WrapRunner.run."""
    params = inspect.signature(WrapRunner.run).parameters
    assert "system_prompt_override" not in params, (
        "HATS-452 regression: WrapRunner.run grew a "
        "system_prompt_override parameter again. HITL has no override "
        "channel — composition flows via build_session_prompt inside "
        "run_session. See ADR-0005 §П2."
    )


def test_subagentrunner_run_keeps_system_prompt_override():
    """Sub-agent path is the Automate runner — HATS-267 prompt injection
    is its legitimate use case. The override channel stays on
    SubAgentRunner.run (semantics: caller-supplied prompt replaces the
    composed injection text)."""
    params = inspect.signature(SubAgentRunner.run).parameters
    assert "system_prompt_override" in params, (
        "SubAgentRunner.run lost the system_prompt_override parameter — "
        "HATS-267 sub-agent custom-prompt use case requires it. The П2 "
        "split is HITL-loses, Automate-keeps."
    )
