"""HATS-948 (T15) — trace vocabulary + session-id env var live in core.

RED-under-revert: moving ``TraceTag``/``ENV_SESSION_ID`` back out of core fails the
import; the ``ai_hats.constants`` shim-parity check guards the integrator surface.
"""

from __future__ import annotations

from ai_hats_core.trace import ENV_SESSION_ID, TraceTag


def test_trace_tag_vocab() -> None:
    assert TraceTag.REQ == "[REQ]"
    assert TraceTag.RES == "[RES]"
    assert ENV_SESSION_ID == "AI_HATS_SESSION_ID"
