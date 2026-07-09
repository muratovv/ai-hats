"""Tracker-local constants (HATS-933).

Duplicated from ``ai_hats.constants`` because the package must not import the
integrator (ADR-0014 boundary); the string value is the shared contract.
"""

from __future__ import annotations

ENV_SESSION_ID = "AI_HATS_SESSION_ID"

# HATS-955: the durable agent-process pid, exported by the integrator harness
# (wrap_runner / subagent_runner). Read here to anchor ownership liveness; the
# ephemeral `ai-hats task` subprocess inherits it from the parent env.
ENV_ROOT_PID = "AI_HATS_ROOT_PID"
