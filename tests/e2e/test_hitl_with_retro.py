"""Live e2e: composition reaches the child AND the child's reply lands
in ``audit.md`` with the canonical ``👾`` marker.

Originally filed as HATS-498's claim-#3 gate ("composition reached child
AND was applied — magic-word echo assertion"). Pre-HATS-535 this required
driving the HITL ``claude`` TUI from outside and was abandoned during
W0-pilot (HATS-473 PoC: continuous ANSI stream → no idle window for PTY
idle-detection). Post-HATS-535 the SubAgent path's ``audit.md`` carries
the same ``👤``/``👾`` markers as HITL (parity gain from the unified
``MakeAudit`` pipeline step), so the SDK-driven ``live_session`` helper
suffices — no TUI driving needed.

This test also serves as HATS-529's audit-capture e2e: it would catch
any regression that drops the ``👾`` marker from ``audit.md`` after the
Path A removal (``SidecarTracer.flush_response`` + ⏺-marker accumulator
were removed; the JSONL parser in ``AuditWriter._parse_jsonl`` is now
the sole audit source).

Gated by both:

* ``@pytest.mark.integration`` — opt out of the default lane (the rest
  of the integration suite gates this too).
* ``skipif(not ANTHROPIC_API_KEY)`` — costs real API tokens (~$0.005
  per run on ``claude-haiku-4-5``); skip on machines without a key.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from ai_hats.observe import SessionManager

from _helpers.live import live_session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY"),
        reason="live API key required; this test spends real tokens",
    ),
]


def test_child_reply_lands_in_audit_md_as_bot_marker(
    probe_project: Path, requires_claude_auth
) -> None:
    """The SubAgent's reply to ``Reply only: alpha`` must appear in
    ``<session_dir>/audit.md`` as ``👾 alpha`` after session finalize.

    Magic-word phrasing chosen for determinism + minimal token cost
    (~10 output tokens); HATS-498's claim-#3 narrative does not require
    a specific phrase, only that the child's literal reply round-trips
    through composition → SDK → JSONL → ``AuditWriter._parse_jsonl`` →
    ``audit.md``.
    """
    captured: dict = {}

    async def _drive():
        async with live_session(
            probe_project,
            role="probe",
            max_budget_usd=0.10,
        ) as s:
            r = await s.send(
                "Reply with exactly one word, lowercase, no punctuation: alpha"
            )
            r.expect_no_error().expect_contains_ci("alpha")
            captured["session_id"] = s.session_id
            captured["reply_text"] = r.text

    asyncio.run(_drive())

    # Locate the session_dir AFTER the `async with` exits — `audit.md`
    # is written by the ``finalize-subagent`` sub-pipeline (HATS-535)
    # which runs in the runner's ``finally`` block.
    session = SessionManager(probe_project).get_session(captured["session_id"])
    audit_path = session.session_dir / "audit.md"
    assert audit_path.exists(), (
        f"audit.md not written at {audit_path} — finalize-subagent pipeline "
        f"may have skipped MakeAudit"
    )

    audit_text = audit_path.read_text()
    # The model's reply must land as a 👾 marker (parser strips trailing
    # punctuation and whitespace; assertion is substring-tolerant to
    # account for "alpha", "alpha.", "alpha\n" etc).
    assert "👾" in audit_text, (
        f"no 👾 marker in audit.md — Path A removal may have regressed "
        f"AuditWriter._parse_jsonl text-block extraction. Audit content "
        f"tail:\n{audit_text[-500:]}"
    )
    assert "alpha" in audit_text.lower(), (
        f"reply word 'alpha' missing from audit.md — composition may not "
        f"have reached the child, OR _parse_jsonl dropped the text block. "
        f"Audit content tail:\n{audit_text[-500:]}"
    )
    # Tighter check: the 👾 line itself must carry 'alpha'.
    bot_lines = [ln for ln in audit_text.splitlines() if ln.startswith("👾")]
    assert bot_lines, "no 👾-prefixed line found in audit.md"
    assert any("alpha" in ln.lower() for ln in bot_lines), (
        f"👾 lines exist but none mention 'alpha'. Lines: {bot_lines}"
    )

    # Symmetry check: the user prompt also lands as 👤.
    user_lines = [ln for ln in audit_text.splitlines() if ln.startswith("👤")]
    assert user_lines, "no 👤-prefixed line in audit.md"
