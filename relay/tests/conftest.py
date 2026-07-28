"""Shared fixtures.

The broker refuses to start unauthenticated (HATS-1232's guardrail), so every test
that starts one carries a token. `ctl()` keeps that from cluttering the assertions.
"""

from __future__ import annotations

import json

import pytest

RELAY_TOKEN = "test-token"  # noqa: S105 — a fixture, not a credential


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch):
    """Keep the developer's shell out of the assertions.

    `--token` defaults to `os.environ.get("HATS_RELAY_TOKEN", "")` on both CLIs, and the
    relay's own `make check-token` tells you to export that variable — so a test asserting
    that default went red or green depending on the shell it ran in, and printed the real
    token into the failure diff (HATS-1288). The one test that DOES care about the variable
    builds its subprocess env explicitly, so scrubbing the parent leaves it untouched.
    """
    monkeypatch.delenv("HATS_RELAY_TOKEN", raising=False)


def ctl(**fields) -> str:
    """A control message with the fixture token already on it."""
    return json.dumps({**fields, "token": RELAY_TOKEN})
