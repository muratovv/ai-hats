"""Tracker-local constants (HATS-933).

Duplicated from ``ai_hats.constants`` because the package must not import the
integrator (ADR-0014 boundary); the string value is the shared contract.
"""

from __future__ import annotations

ENV_SESSION_ID = "AI_HATS_SESSION_ID"
