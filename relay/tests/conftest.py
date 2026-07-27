"""Shared fixtures.

The broker refuses to start unauthenticated (HATS-1232's guardrail), so every test
that starts one carries a token. `ctl()` keeps that from cluttering the assertions.
"""

from __future__ import annotations

import json

RELAY_TOKEN = "test-token"  # noqa: S105 — a fixture, not a credential


def ctl(**fields) -> str:
    """A control message with the fixture token already on it."""
    return json.dumps({**fields, "token": RELAY_TOKEN})
