"""W0 pilot v2 — validate the e2e framework against the real SDK.

The original W0 pilot used a PTY + idle-detection approach. That
foundation didn't survive contact with the real claude TUI (HATS-473
PoC verdict: continuous ANSI stream → no "idle" window ever opens).
The framework moved to :mod:`claude_agent_sdk` for sub-agents
(HATS-474), so the pilot is rewritten against the new surface.

This is **the** sanity test for the new e2e framework — if it passes,
:func:`tests.e2e._helpers.live.live_session` + chainable assertion
verbs + budget-cap fixtures all work end-to-end against the bundled
claude binary. If it fails, framework corrections land before any
further e2e tests build on top of it.

Three scenarios cover the verbs the framework exposes:

1. ``test_w0_live_session_helper_api`` — happy path. Single
   :meth:`send`, every per-turn ``expect_*`` verb chains, every
   session-wide ``expect_*`` verb passes. Smoke test for the surface.
2. ``test_w0_live_session_tool_use_detected`` — agent is asked to use
   a tool (Read). Asserts ``expect_tool_used`` finds the call in the
   per-turn ``Response.tool_calls`` derivation, end-to-end.
3. ``test_w0_live_session_multi_turn_state`` — two-turn dialog,
   asserts session-wide aggregator (``send_count``, summed
   ``total_cost_usd``, stable ``claude_session_id``).

Cost-capped via ``model="claude-haiku-4-5"`` and
``max_budget_usd=0.10`` per session. Expected ~$0.005 per file run.

Fail-under-revert (``dev_rule_e2e_gate`` §4): the framework lives in
``tests/e2e/_helpers/live.py`` shipped in HATS-474 Phase 4. Reverting
that file makes the imports fail at collection time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from _helpers.live import LiveSession, TurnResult, live_session


pytestmark = pytest.mark.integration


def test_w0_live_session_helper_api(
    probe_project: Path, requires_claude_auth
) -> None:
    """Chainable assertion verbs all return ``self`` and pass for a
    deterministic single-turn dialog."""
    captured: dict = {}

    async def _drive():
        async with live_session(
            probe_project,
            role="probe",
            max_budget_usd=0.10,
        ) as s:
            assert isinstance(s, LiveSession)

            r = await s.send(
                "Reply with exactly the three characters: OK!. No other text."
            )
            assert isinstance(r, TurnResult)

            # Chain per-turn verbs — each returns self.
            (
                r
                .expect_no_error()
                .expect_contains_ci("ok")
                .expect_cost_under(0.05)
                .expect_no_tools()  # nothing for the agent to use here
            )
            captured["r_text"] = r.text
            captured["r_cost"] = r.cost_usd

            # Chain session-wide verbs.
            (
                s
                .expect_no_error()
                .expect_send_count(1)
                .expect_claude_session_id()
                .expect_cost_under(0.10)
            )
            captured["session_id"] = s.session_id
            captured["claude_session_id"] = s.claude_session_id
            captured["total_cost"] = s.total_cost_usd

    asyncio.run(_drive())

    assert "ok" in captured["r_text"].lower()
    assert captured["claude_session_id"], "no SDK round-trip happened"
    assert captured["r_cost"] is not None
    assert captured["total_cost"] is not None
    assert captured["total_cost"] >= captured["r_cost"]  # session sums turns


def test_w0_live_session_tool_use_detected(
    probe_project: Path, requires_claude_auth, tmp_path: Path,
) -> None:
    """Agent is asked to read a sentinel file; ``expect_tool_used``
    must find the matching call in the per-turn ``tool_calls`` view.

    The sentinel file lives outside ``probe_project`` so the agent
    can't observe it via implicit project discovery — it must use the
    Read (or Bash) tool to get the content."""
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("HATS-474-PHASE-4-OK\n")

    captured: dict = {}

    async def _drive():
        async with live_session(
            probe_project,
            role="probe",
            max_budget_usd=0.15,
            # Pre-approve Read + Bash so the agent doesn't stop at a
            # permission prompt — the test is about *whether* the tool
            # was invoked, not about the permission UX.
            allowed_tools=["Read", "Bash"],
        ) as s:
            r = await s.send(
                f"Use the Read tool to read the file at exactly this path:\n"
                f"  {sentinel}\n"
                f"Then reply with ONLY the contents of that file. "
                f"No commentary, no markdown fences. Just the contents."
            )
            r.expect_no_error()
            # At least one of {Read, Bash} should have been used; agents
            # sometimes pick Bash(cat) over Read. Both count.
            used = [c.name for c in r.tool_calls]
            captured["used"] = used
            captured["text"] = r.text
            assert used, (
                f"agent used no tools — expected Read or Bash. "
                f"Response: {r.text[-300:]!r}"
            )
            assert any(name in ("Read", "Bash") for name in used), (
                f"expected Read or Bash tool; got {used}"
            )
            # The sentinel content must reach the response.
            r.expect_contains("HATS-474-PHASE-4-OK")

    asyncio.run(_drive())


def test_w0_live_session_multi_turn_state(
    probe_project: Path, requires_claude_auth,
) -> None:
    """Two-turn dialog: send count and aggregated cost track sends
    correctly; claude_session_id stays stable across turns."""
    captured: dict = {}

    async def _drive():
        async with live_session(
            probe_project,
            role="probe",
            max_budget_usd=0.20,
        ) as s:
            r1 = await s.send(
                "Pick a one-word topic. Reply with just the word."
            )
            r1.expect_no_error()
            captured["sid_after_t1"] = s.claude_session_id

            r2 = await s.send(
                "Now write a one-sentence definition of that word."
            )
            r2.expect_no_error()
            captured["sid_after_t2"] = s.claude_session_id

            s.expect_send_count(2).expect_no_error()
            captured["cost"] = s.total_cost_usd

    asyncio.run(_drive())

    # claude_session_id stable across turns — single live SDK client,
    # no --resume plumbing needed (the foundational win of moving off
    # PoC-2's subprocess-per-turn).
    assert captured["sid_after_t1"]
    assert captured["sid_after_t1"] == captured["sid_after_t2"]
    assert captured["cost"] is not None and captured["cost"] < 0.20
