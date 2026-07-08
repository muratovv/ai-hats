"""Diagnostic helpers for harness-layer failures (HATS-378).

Lifted from :mod:`ai_hats.retro.session_review_runner` (HATS-271) so the
universal harness guard reuses the same diagnostic format the per-role
empty-transcript check has been using since v0.5.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..paths import REASONING_LOG

if TYPE_CHECKING:
    from ai_hats_observe import Session


def is_zero_output(metrics: dict[str, Any]) -> bool:
    """True iff a finalized sub-agent run produced no observable output.

    Both ``tokens.output`` AND ``tool_calls`` must be zero. A run that
    emitted tool calls but no tokens (or vice versa) is *not* zero-output
    — those are validation failures handled by per-role logic.

    Returns ``False`` when either field is absent from metrics — the
    guard cannot evaluate sub-agent runs whose metrics have not been
    trace-enriched (basic ``_finalize_sub_agent`` only writes exit_code/
    role/model). For those, the per-role transcript check (HATS-271)
    remains the safety net until sub-agent enrichment lands as a
    follow-up.
    """
    tokens = metrics.get("tokens")
    if not isinstance(tokens, dict) or "output" not in tokens:
        return False
    if "tool_calls" not in metrics:
        return False
    tokens_out = tokens.get("output") or 0
    tool_calls = metrics.get("tool_calls") or 0
    return tokens_out == 0 and tool_calls == 0


def diagnose_silent_session(session: "Session") -> str:
    """Summarise *why* a sub-agent session produced no useful output.

    Reads ``metrics.json`` + ``reasoning.log`` from the sub-session dir
    to expose ``exit_code``, ``timed_out``, ``error``, and a stderr tail
    — so the failure message in retro.log explains *why* the agent
    produced nothing instead of just "Empty frontmatter".

    Used by both the per-role empty-transcript check (HATS-271, kept as
    defense-in-depth) and the universal zero-output guard (HATS-378).
    """
    bits: list[str] = [f"sub-session={session.session_id}"]
    try:
        if session.metrics_path.exists():
            metrics = json.loads(session.metrics_path.read_text())
            for key in ("exit_code", "timed_out", "error"):
                if key in metrics and metrics[key] not in (None, False):
                    bits.append(f"{key}={metrics[key]}")
    except (OSError, ValueError):
        bits.append("metrics=unreadable")
    reasoning = session.session_dir / REASONING_LOG
    if reasoning.exists():
        try:
            tail = reasoning.read_text()[-300:].strip()
            if tail:
                bits.append(f"stderr_tail={tail!r}")
        except OSError:
            pass
    return "; ".join(bits)
